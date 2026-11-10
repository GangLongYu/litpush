"""人工补录的摘要。

少数出版社（尤其 Elsevier 的 AST、部分 AIP 刊）既不给 OpenAlex 摘要，
Crossref 也没有，卡片就只能靠标题猜。这里提供一个兜底：

    data/abstracts.json      {"10.1016/j.ast.2026.114064": "摘要原文 …"}

命中后会在「Crossref 兜底」之后覆盖，之后每天重跑都会自动带上，
不需要改代码。也可以在卡片里看到 `#人工补录` 的标记。
"""

from __future__ import annotations

import json
from pathlib import Path

from .model import Paper


class AbstractOverrides:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(raw, dict):
            self.data = {str(k).lower().strip(): str(v).strip()
                         for k, v in raw.items() if str(v).strip()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8", newline="\n")

    def add(self, doi: str, abstract: str) -> str:
        key = (doi or "").lower().strip()
        if not key:
            raise ValueError("DOI 不能为空")
        self.data[key] = " ".join(abstract.split())
        self.save()
        return key

    def apply(self, paper: Paper) -> bool:
        text = self.data.get((paper.doi or "").lower().strip())
        if not text:
            return False
        if len(text) <= len(paper.abstract or ""):
            return False
        paper.abstract = text
        if "人工补录" not in paper.sources:
            paper.sources.append("人工补录")
        return True
