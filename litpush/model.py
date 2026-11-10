"""文献数据模型与通用小工具。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any

_WIN_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class TopicMatch:
    topic_id: str
    label: str
    weight: float
    priority: str = "secondary"
    title_hits: list[str] = field(default_factory=list)
    abstract_hits: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.title_hits) + len(self.abstract_hits)

    @property
    def sample(self) -> str:
        pool = self.title_hits or self.abstract_hits
        return pool[0] if pool else ""


@dataclass
class AdvisorCitation:
    """文章引用的某一篇关注学者论文。"""

    title: str
    year: int | None = None
    doi: str = ""
    url: str = ""

    def label(self) -> str:
        year = f" ({self.year})" if self.year else ""
        return f"{self.title}{year}"


@dataclass
class Paper:
    key: str
    title: str
    abstract: str = ""
    authors: list[str] = field(default_factory=list)
    journal: str = ""
    issn: str = ""
    doi: str = ""
    oa_id: str = ""
    url: str = ""
    pdf_url: str = ""
    published: str = ""
    year: int | None = None
    type: str = "article"
    cited_by: int = 0
    keywords: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    tier: int = 3
    is_preprint: bool = False
    volume: str = ""
    issue: str = ""
    pages: str = ""
    matches: list[TopicMatch] = field(default_factory=list)
    score: float = 0.0
    priority: str = "C"
    advisor_cites: list[AdvisorCitation] = field(default_factory=list)
    score_parts: dict[str, float] = field(default_factory=dict)

    # ----------------------------------------------------------- 便捷属性
    @property
    def first_author(self) -> str:
        return self.authors[0] if self.authors else "Unknown"

    @property
    def author_line(self) -> str:
        if not self.authors:
            return "未知作者"
        if len(self.authors) <= 3:
            return ", ".join(self.authors)
        return f"{self.authors[0]} et al."

    @property
    def topic_labels(self) -> list[str]:
        order = {"core": 0, "secondary": 1, "tertiary": 2}
        items = sorted(self.matches, key=lambda m: (order.get(m.priority, 3), -m.weight))
        return [m.label for m in items]

    @property
    def has_advisor_cite(self) -> bool:
        return bool(self.advisor_cites)

    @property
    def is_review(self) -> bool:
        text = (self.title + " " + self.abstract[:400]).lower()
        return bool(re.search(r"\b(review|survey|overview|progress in|state of the art)\b", text))

    def short_title(self, limit: int = 70) -> str:
        title = " ".join(self.title.split())
        if len(title) <= limit:
            return title
        cut = title[:limit].rsplit(" ", 1)[0]
        return cut + "…"

    def slug(self, limit: int = 60) -> str:
        """生成 Obsidian/Windows 安全文件名。"""
        base = f"{self.year or ''} {self.first_author} - {self.title}"
        base = unicodedata.normalize("NFKC", base)
        base = _WIN_BAD.sub(" ", base)
        base = re.sub(r"\s+", " ", base).strip(" .")
        if len(base) > limit:
            base = base[:limit].rsplit(" ", 1)[0]
        return base.strip(" .") or (self.doi or "untitled")


def normalize_key(doi: str = "", oa_id: str = "", title: str = "") -> str:
    doi = (doi or "").strip().lower()
    if doi:
        return "doi:" + doi
    if oa_id:
        return "oa:" + oa_id.rsplit("/", 1)[-1]
    return "title:" + re.sub(r"\W+", "", title.lower())[:120]


def to_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)          # 去掉 JATS/HTML 标签
    text = text.replace("\u00a0", " ").replace("\u2009", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def merge_paper(base: Paper, extra: Paper) -> Paper:
    """合并同一篇文献的多来源记录，保留信息更全的字段。"""
    if len(extra.abstract) > len(base.abstract):
        base.abstract = extra.abstract
    if not base.doi and extra.doi:
        base.doi = extra.doi
        base.url = base.url or extra.url
    if not base.pdf_url and extra.pdf_url:
        base.pdf_url = extra.pdf_url
    if not base.keywords and extra.keywords:
        base.keywords = extra.keywords
    if extra.tier < base.tier:
        base.tier = extra.tier
        base.is_preprint = extra.is_preprint
    if len(extra.authors) > len(base.authors):
        base.authors = extra.authors
    if extra.cited_by > base.cited_by:
        base.cited_by = extra.cited_by
    for src in extra.sources:
        if src not in base.sources:
            base.sources.append(src)
    return base


def paper_to_dict(p: Paper) -> dict[str, Any]:
    return {
        "key": p.key,
        "title": p.title,
        "doi": p.doi,
        "oa_id": p.oa_id,
        "journal": p.journal,
        "published": p.published,
        "year": p.year,
        "score": round(p.score, 2),
        "priority": p.priority,
        "topics": p.topic_labels,
        "sources": p.sources,
        "advisor_cites": [c.label() for c in p.advisor_cites],
    }
