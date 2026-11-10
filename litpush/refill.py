"""补/重生成单篇卡片，并把日记里对应的速览也一起更新。

场景：某篇文献出版社没给摘要，卡片只能靠标题猜；你从 PDF 里把摘要贴进来，
用 `litpush refill --doi 10.xxx --abstract-file a.txt` 就能重做这一张，
不动其他卡片、也不重跑整天。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import journal as journal_mod
from . import llm
from .classify import TopicMatcher, score_paper
from .config import Config
from .crossref import abstract_by_doi
from .http import ApiClient
from .model import Paper
from .openalex import short_id, work_by_doi, work_to_paper
from .overrides import AbstractOverrides
from .render import _compact_card, note_filename, render_paper_note
from .summarize import summarize


class Refiller:
    def __init__(self, cfg: Config, *, log=print):
        self.cfg = cfg
        self.log = log
        self.data_dir = Path(cfg.path).resolve().parent.parent / "data"
        self.client = ApiClient(
            self.data_dir / "cache",
            ttl_hours=cfg.general.cache_ttl_hours,
            interval=cfg.general.request_interval,
            max_retries=cfg.general.max_retries,
            user_agent=cfg.general.user_agent,
            polite_email=cfg.general.polite_email,
        )
        self.overrides = AbstractOverrides(self.data_dir / "abstracts.json")
        self.matcher = TopicMatcher(cfg.topics)

    # ------------------------------------------------------------------ 构建
    def build(self, doi: str, abstract: str = "") -> Paper:
        if abstract:
            self.overrides.add(doi, abstract)
            self.log(f"已把摘要写入 {self.overrides.path}")

        work = work_by_doi(self.client, doi, api_key=self.cfg.general.openalex_api_key)
        paper: Paper = work_to_paper(work, "补录")
        for jr in self.cfg.journals:
            if jr.issn and paper.issn and jr.issn == paper.issn:
                paper.journal, paper.tier = jr.name, jr.tier
                break
            if jr.name.lower() == paper.journal.lower():
                paper.tier = jr.tier
                break
        paper.matches = self.matcher.match(paper)
        score_paper(paper, self.cfg)

        if self.overrides.apply(paper):
            self.log("使用 data/abstracts.json 里的人工补录摘要")
        elif not paper.abstract:
            fetched = abstract_by_doi(self.client, paper.doi)
            if fetched:
                paper.abstract = fetched
                self.log("Crossref 补到了摘要")
        if paper.has_advisor_cite is False:
            pass
        return paper

    def summarize(self, paper: Paper, use_llm: bool = True) -> object:
        summary = summarize(paper, self.cfg)
        if use_llm and self.cfg.llm.enabled and self.cfg.llm.api_key():
            polished = llm.polish(paper, summary, self.cfg)
            if polished:
                summary = polished
                self.log("大模型润色完成")
            else:
                self.log("大模型调用失败，使用规则版摘要")
        return summary

    # ------------------------------------------------------------------ 写卡片
    def write_card(self, paper: Paper, summary, day: date) -> Path:
        subdir = self.cfg.general.per_paper_subdir
        target_dir = self.cfg.output_dir / subdir if subdir else self.cfg.output_dir
        name = note_filename(paper)
        path = target_dir / f"{name}.md"
        text = render_paper_note(paper, summary, self.cfg, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        self.log(f"卡片已更新：{path}")
        return path

    # ------------------------------------------------------------------ 日记
    def patch_journal(self, paper: Paper, summary, day: date) -> bool:
        settings = journal_mod.load_settings(
            self.cfg.journal.vault,
            folder=self.cfg.journal.folder,
            date_format=self.cfg.journal.format,
            template=self.cfg.journal.template,
            weekday_locale=self.cfg.journal.weekday_locale,
        )
        note = settings.path_for(day)
        if not note.exists():
            return False
        text = note.read_text(encoding="utf-8")
        name = note_filename(paper)
        lines = text.splitlines()
        link_marker = f"[[{name}|📄 精读卡片]]"
        idx_link = next((i for i, line in enumerate(lines) if link_marker in line), None)
        if idx_link is None:
            return False

        start = idx_link
        while start > 0 and not lines[start].startswith("### "):
            start -= 1
        if not lines[start].startswith("### "):
            return False
        number = re.match(r"###\s*(\d+)\.", lines[start])
        number = int(number.group(1)) if number else 1

        end = idx_link + 1
        while end < len(lines) and not (
            lines[end].startswith("### ") or lines[end].startswith("#### ")
            or lines[end].startswith("<!-- litpush:end")
        ):
            end += 1

        card = _compact_card(paper, summary, number, name)
        new_lines = lines[:start] + card + [""] + lines[end:]
        note.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8", newline="\n")
        self.log(f"日记里的速览已同步：{note.name}")
        return True

    # --------------------------------------------------------------- 批量重排
    def rerender_cards(self, limit: int = 0) -> int:
        """把已有卡片按当前模板重排一遍（只读 frontmatter 里的 DOI，不调大模型）。"""
        target_dir = self.cfg.output_dir
        if self.cfg.general.per_paper_subdir:
            target_dir = target_dir / self.cfg.general.per_paper_subdir
        done = 0
        for path in sorted(target_dir.glob("*.md")):
            if limit and done >= limit:
                break
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                self.log(f"跳过（读不了）：{path.name} — {exc}")
                continue
            match = re.search(r'^doi:\s*"?([^"\n]+)"?\s*$', text, re.M)
            if not match or not match.group(1).strip():
                self.log(f"跳过（无 DOI）：{path.name}")
                continue
            doi = match.group(1).strip()
            try:
                paper = self.build(doi)
            except Exception as exc:  # noqa: BLE001 - 网络/接口问题，继续下一篇
                self.log(f"跳过（取不到记录）：{path.name} — {exc}")
                continue
            name = note_filename(paper)
            out = target_dir / f"{name}.md"
            out.write_text(render_paper_note(paper, None, self.cfg, date.today()),
                           encoding="utf-8", newline="\n")
            if out != path:
                try:
                    path.unlink()
                except OSError:
                    self.log(f"  旧文件删不掉，请手动删除：{path.name}")
            done += 1
        return done
