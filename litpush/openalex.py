"""OpenAlex 数据访问层。

为什么只用 OpenAlex：
- 一个接口同时覆盖目标期刊、arXiv 预印本、引用关系（cites: 过滤器）；
- 元数据规范（DOI/作者/机构/摘要倒排索引都齐全）；
- 每天约 15 次请求即可覆盖全部需求，适合长期无人值守运行。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

from .http import ApiClient, HttpError
from .model import Paper, clean_text, normalize_key

BASE = "https://api.openalex.org"
WORKS = BASE + "/works"
SOURCES = BASE + "/sources"
AUTHORS = BASE + "/authors"

_OPENALEX_ID = re.compile(r"(W\d+|A\d+|S\d+)$")


def short_id(value: str) -> str:
    """https://openalex.org/W123 -> W123"""
    if not value:
        return ""
    match = _OPENALEX_ID.search(value.strip())
    return match.group(1) if match else value.strip()


def abstract_from_index(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    slots: list[tuple[int, str]] = []
    for word, positions in index.items():
        for pos in positions:
            slots.append((pos, word))
    slots.sort()
    return " ".join(word for _, word in slots)


def _authors(work: dict[str, Any], limit: int = 30) -> list[str]:
    names = []
    for authorship in work.get("authorships") or []:
        name = (authorship.get("author") or {}).get("display_name")
        if name:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def work_to_paper(work: dict[str, Any], source_label: str = "") -> Paper:
    primary = work.get("primary_location") or {}
    journal_obj = primary.get("source") or {}
    journal = clean_text(journal_obj.get("display_name")) or "未知来源"
    issn = journal_obj.get("issn_l") or ""
    doi = (work.get("doi") or "").replace("https://doi.org/", "").strip()
    oa_id = short_id(work.get("id", ""))
    pub = work.get("publication_date") or ""
    year = work.get("publication_year")

    best = work.get("best_oa_location") or {}
    pdf_url = best.get("pdf_url") or ""
    if not pdf_url:
        for loc in work.get("locations") or []:
            if loc.get("pdf_url"):
                pdf_url = loc["pdf_url"]
                break

    url = ""
    if doi:
        url = "https://doi.org/" + doi
    elif primary.get("landing_page_url"):
        url = primary["landing_page_url"]
    elif oa_id:
        url = f"https://openalex.org/{oa_id}"

    keywords = [clean_text(k.get("display_name")) for k in (work.get("keywords") or [])]
    keywords = [k for k in keywords if k][:8]

    paper = Paper(
        key=normalize_key(doi=doi, oa_id=oa_id, title=work.get("title") or ""),
        title=clean_text(work.get("title") or work.get("display_name") or "无标题"),
        abstract=abstract_from_index(work.get("abstract_inverted_index")),
        authors=_authors(work),
        journal=journal,
        issn=issn or "",
        doi=doi,
        oa_id=oa_id,
        url=url,
        pdf_url=pdf_url or "",
        published=pub,
        year=int(year) if isinstance(year, int) else None,
        type=work.get("type") or "article",
        cited_by=int(work.get("cited_by_count") or 0),
        keywords=keywords,
        volume=str(work.get("volume") or ""),
        issue=str(work.get("issue") or ""),
        pages=((work.get("biblio") or {}).get("first_page") or ""),
    )
    pre = journal.lower()
    paper.is_preprint = paper.type in {"preprint", "posted-content"} or "arxiv" in pre
    if "arxiv" in pre:
        paper.journal = "arXiv 预印本"
    if source_label:
        paper.sources.append(source_label)
    return paper


# ------------------------------------------------------------------ sources
def resolve_source(client: ApiClient, issn: str, name: str, cache_path: Path) -> str | None:
    """按 ISSN 解析 OpenAlex source id；结果落盘缓存，避免每天重复解析。"""
    cache: dict[str, Any] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}
    key = issn or name
    entry = cache.get(key)
    if isinstance(entry, dict) and entry.get("id"):
        return entry["id"]

    found: str | None = None
    display = name
    if issn:
        data = client.get_json(SOURCES, {"filter": f"issn:{issn}", "per-page": 3})
        for src in data.get("results", []):
            found = short_id(src.get("id", ""))
            display = src.get("display_name") or name
            break
    if not found:
        data = client.get_json(SOURCES, {"search": name, "per-page": 3})
        for src in data.get("results", []):
            if (src.get("issn_l") or "") == issn or issn in (src.get("issn") or []):
                found = short_id(src.get("id", ""))
                display = src.get("display_name") or name
                break
        if not found and data.get("results"):
            found = short_id(data["results"][0].get("id", ""))
            display = data["results"][0].get("display_name") or name

    if found:
        cache[key] = {"id": found, "name": display, "issn": issn}
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return found


# --------------------------------------------------------------------- works
def iter_works(
    client: ApiClient,
    filters: list[str],
    *,
    per_page: int = 200,
    max_records: int = 5000,
    sort: str = "publication_date:desc",
    api_key: str = "",
) -> Iterator[dict[str, Any]]:
    """翻页抓取。

    注意：OpenAlex 自 2025 年起对 cursor 深翻页要求 API key，未配置 key 时
    用 page 翻页（页数 × 200 不超过 1 万条，对本工具完全够用）。
    """
    page = 1
    fetched = 0
    while fetched < max_records:
        params: dict[str, Any] = {
            "filter": ",".join(filters),
            "per-page": min(per_page, max_records - fetched),
            "page": page,
            "sort": sort,
        }
        if api_key:
            params["api_key"] = api_key
        data = client.get_json(WORKS, params)
        results = data.get("results") or []
        for item in results:
            yield item
        fetched += len(results)
        total = (data.get("meta") or {}).get("count") or 0
        if not results or fetched >= total:
            break
        page += 1


def journal_batch(
    client: ApiClient,
    source_ids: list[str],
    since: date,
    until: date,
    *,
    max_records: int = 4000,
    api_key: str = "",
) -> list[dict[str, Any]]:
    """一次请求覆盖多个期刊（source id 用 | 做 OR）。"""
    if not source_ids:
        return []
    src = "primary_location.source.id:" + "|".join(source_ids)
    filters = [src, f"from_publication_date:{since.isoformat()}",
               f"to_publication_date:{until.isoformat()}"]
    return list(iter_works(client, filters, max_records=max_records, api_key=api_key))


def search_works(
    client: ApiClient,
    query: str,
    since: date,
    until: date,
    *,
    per_page: int = 50,
    api_key: str = "",
) -> list[dict[str, Any]]:
    filters = [
        f"from_publication_date:{since.isoformat()}",
        f"to_publication_date:{until.isoformat()}",
        "type:article|preprint|review",
    ]
    params: dict[str, Any] = {
        "search": query,
        "filter": ",".join(filters),
        "per-page": per_page,
        "sort": "publication_date:desc",
    }
    if api_key:
        params["api_key"] = api_key
    data = client.get_json(WORKS, params)
    return data.get("results") or []


def citing_works(
    client: ApiClient,
    work_ids: list[str],
    since: date,
    until: date,
    *,
    chunk: int = 40,
    max_records: int = 600,
    api_key: str = "",
) -> list[dict[str, Any]]:
    """找出引用了给定文献集合（OR）的所有文章。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    ids = [short_id(w) for w in work_ids if w]
    for start in range(0, len(ids), chunk):
        part = ids[start:start + chunk]
        filters = [f"cites:{'|'.join(part)}",
                   f"from_publication_date:{since.isoformat()}",
                   f"to_publication_date:{until.isoformat()}"]
        for work in iter_works(client, filters, max_records=max_records, api_key=api_key):
            wid = short_id(work.get("id", ""))
            if wid and wid not in seen:
                seen.add(wid)
                out.append(work)
    return out


# -------------------------------------------------------------------- author
def author_id_by_orcid(client: ApiClient, orcid: str) -> str | None:
    if not orcid:
        return None
    try:
        data = client.get_json(f"{AUTHORS}/https://orcid.org/{orcid}")
        return short_id(data.get("id", ""))
    except HttpError:
        return None


def author_works(
    client: ApiClient, author_id: str, *, per_page: int = 200
) -> list[dict[str, Any]]:
    if not author_id:
        return []
    results: list[dict[str, Any]] = []
    cursor = "*"
    while cursor:
        data = client.get_json(
            WORKS,
            {
                "filter": f"author.id:{short_id(author_id)}",
                "per-page": per_page,
                "cursor": cursor,
                "select": "id,doi,title,publication_year,cited_by_count,type",
            },
        )
        batch = data.get("results") or []
        results.extend(batch)
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not batch:
            break
    return results


def work_by_doi(client: ApiClient, doi: str, api_key: str = "") -> dict[str, Any]:
    """按 DOI 取单条记录（补卡片用）。"""
    doi = (doi or "").strip()
    if not doi:
        raise ValueError("DOI 不能为空")
    if doi.lower().startswith("https://doi.org/"):
        doi = doi[16:]
    url = f"{WORKS}/https://doi.org/{doi}"
    return client.get_json(url, {"api_key": api_key} if api_key else None)


def work_titles(client: ApiClient, work_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """按 id 批量取标题/年份（引用追踪时用来显示"引用了关注学者哪篇"）。"""
    ids = [short_id(w) for w in work_ids if w]
    if not ids:
        return {}
    data = client.get_json(
        WORKS,
        {"filter": f"ids.openalex:{'|'.join(ids[:50])}", "per-page": 50,
         "select": "id,doi,title,publication_year"},
    )
    out: dict[str, dict[str, Any]] = {}
    for work in data.get("results") or []:
        out[short_id(work.get("id", ""))] = work
    return out
