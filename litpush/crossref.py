"""Crossref 兜底：补齐 OpenAlex 缺失的摘要。

Elsevier / Springer 的期刊（AST、Acta Mech Sin 等）在 OpenAlex 里常常没有摘要，
Crossref 记录里却带着 JATS 摘要，取一次即可显著提升摘要质量。
"""

from __future__ import annotations

import re
import urllib.parse

from .http import ApiClient
from .model import clean_text


def abstract_by_doi(client: ApiClient, doi: str) -> str:
    if not doi:
        return ""
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    try:
        data = client.get_json(url, polite=False)
    except Exception:  # noqa: BLE001 - 兜底来源，失败就放弃
        return ""
    message = data.get("message") or {}
    abstract = clean_text(message.get("abstract") or "")
    abstract = re.sub(r"^(Abstract|ABSTRACT)\s*[:：]?\s*", "", abstract)
    return abstract if len(abstract) > 80 else ""
