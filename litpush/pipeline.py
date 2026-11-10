"""主流程：采集 → 去重 → 打分 → 生成摘要 → 写 Obsidian 笔记。"""

from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import journal, llm
from .classify import TopicMatcher, is_relevant, score_paper
from .config import Config, Journal
from .http import ApiClient, brief_error
from .model import Paper, merge_paper
from .openalex import (
    author_id_by_orcid,
    author_works,
    citing_works,
    journal_batch,
    resolve_source,
    search_works,
    short_id,
    work_to_paper,
)
from .render import (
    note_filename,
    render_daily_note,
    render_index,
    render_journal_block,
    render_journal_supplement,
    render_paper_note,
    write_text,
)
from .store import RunStats, Store
from .summarize import Summary, summarize


@dataclass
class RunResult:
    digest_date: date
    stats: RunStats
    papers: list[Paper] = field(default_factory=list)
    advisor_papers: list[Paper] = field(default_factory=list)
    digest_path: Path | None = None
    notes: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False
    preview: str = ""
    failed: bool = False
    fail_reason: str = ""


class Pipeline:
    def __init__(self, cfg: Config, *, log=print, dry_run: bool = False):
        self.cfg = cfg
        self.log = log
        self.dry_run = dry_run
        self.data_dir = Path(cfg.path).resolve().parent.parent / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.client = ApiClient(
            self.data_dir / "cache",
            ttl_hours=cfg.general.cache_ttl_hours,
            interval=cfg.general.request_interval,
            max_retries=cfg.general.max_retries,
            user_agent=cfg.general.user_agent,
            polite_email=cfg.general.polite_email,
        )
        self.store = Store(self.data_dir / "litpush.sqlite3")
        self.matcher = TopicMatcher(cfg.topics)
        self.warnings: list[str] = []
        self.notes_paths: list[Path] = []
        self.issn_index = {j.issn: j for j in cfg.journals if j.issn}

    # ------------------------------------------------------------------ 采集
    def _journal_for(self, paper: Paper) -> Journal | None:
        if paper.issn and paper.issn in self.issn_index:
            return self.issn_index[paper.issn]
        for journal in self.cfg.journals:
            if journal.name.lower() == paper.journal.lower():
                return journal
        return None

    def collect_journals(self, today: date) -> list[Paper]:
        source_cache = self.data_dir / "sources.json"
        groups: dict[int, list[tuple[Journal, str]]] = {}
        for journal in self.cfg.journals:
            if not journal.enabled:
                continue
            sid = resolve_source(self.client, journal.issn, journal.name, source_cache)
            if not sid:
                self.warnings.append(f"未能解析期刊 {journal.name}（ISSN {journal.issn}），已跳过")
                continue
            groups.setdefault(journal.lookback_days, []).append((journal, sid))

        papers: list[Paper] = []
        for lookback, items in sorted(groups.items()):
            since = today - timedelta(days=lookback)
            ids = [sid for _, sid in items]
            names = {sid: j for j, sid in items}
            try:
                works = journal_batch(self.client, ids, since, today,
                                      api_key=self.cfg.general.openalex_api_key)
            except Exception as exc:  # noqa: BLE001
                self.warnings.append(f"期刊批量抓取失败（回溯 {lookback} 天）：{brief_error(exc)}")
                continue
            for work in works:
                paper = work_to_paper(work, "期刊扫描")
                journal = self._journal_for(paper) or names.get(
                    short_id((work.get("primary_location") or {}).get("source", {}).get("id", "") or ""))
                if journal:
                    paper.journal = journal.name
                    paper.tier = journal.tier
                if paper.is_preprint and not self.cfg.general.include_preprints:
                    continue
                papers.append(paper)
            self.log(f"  期刊批次（回溯 {lookback} 天）：{len(works)} 篇")
        return papers

    def collect_searches(self, today: date) -> list[Paper]:
        papers: list[Paper] = []
        for item in self.cfg.searches:
            since = today - timedelta(days=item.days)
            try:
                works = search_works(self.client, item.query, since, today,
                                     api_key=self.cfg.general.openalex_api_key)
            except Exception as exc:  # noqa: BLE001
                self.warnings.append(f"主题检索「{item.query}」失败：{brief_error(exc)}")
                continue
            for work in works:
                paper = work_to_paper(work, "主题检索")
                journal = self._journal_for(paper)
                if journal:
                    paper.journal = journal.name
                    paper.tier = journal.tier
                papers.append(paper)
            self.log(f"  主题检索「{item.query}」：{len(works)} 篇")
        return papers

    # ------------------------------------------------------------------ 关注学者
    def _advisor_works(self) -> dict[str, dict]:
        cached = self.store.get_advisor_works()
        stale = True
        if cached:
            updated = cached[0].get("updated") or ""
            try:
                age = (datetime.now() - datetime.fromisoformat(updated)).days
                stale = age >= self.cfg.advisor.refresh_days
            except ValueError:
                stale = True
        if stale or not cached:
            aid = self.cfg.advisor.openalex_id or author_id_by_orcid(
                self.client, self.cfg.advisor.orcid)
            if not aid:
                self.warnings.append("未能解析关注学者的 OpenAlex 作者 ID，跳过引用追踪")
                return {w["id"]: w for w in cached}
            works = author_works(self.client, aid)
            rows = []
            for work in works:
                rows.append({
                    "id": short_id(work.get("id", "")),
                    "title": work.get("title") or "",
                    "year": work.get("publication_year"),
                    "doi": (work.get("doi") or "").replace("https://doi.org/", ""),
                    "cited": work.get("cited_by_count") or 0,
                })
            rows = [r for r in rows if r["id"]]
            self.store.set_advisor_works(rows)
            cached = self.store.get_advisor_works()
            self.log(f"  已刷新关注学者著作列表：{len(cached)} 篇")
        return {row["id"]: row for row in cached}

    def collect_advisor(self, today: date) -> tuple[list[Paper], dict[str, dict]]:
        if not self.cfg.advisor.enabled:
            return [], {}
        works = self._advisor_works()
        if not works:
            return [], {}
        since = today - timedelta(days=self.cfg.advisor.lookback_days)
        try:
            raw = citing_works(self.client, list(works.keys()), since, today,
                               api_key=self.cfg.general.openalex_api_key)
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"引用追踪失败：{brief_error(exc)}")
            return [], works

        from .model import AdvisorCitation

        papers: list[Paper] = []
        for work in raw:
            paper = work_to_paper(work, "引用关注学者")
            journal = self._journal_for(paper)
            if journal:
                paper.journal = journal.name
                paper.tier = journal.tier
            refs = {short_id(r) for r in (work.get("referenced_works") or [])}
            hit = refs & set(works.keys())
            for wid in list(hit)[:3]:
                info = works[wid]
                paper.advisor_cites.append(AdvisorCitation(
                    title=info.get("title") or "",
                    year=info.get("year"),
                    doi=info.get("doi") or "",
                ))
            papers.append(paper)
        self.log(f"  引用关注学者工作：{len(papers)} 篇（回溯 {self.cfg.advisor.lookback_days} 天）")
        return papers, works

    # ------------------------------------------------------------------ 主流程
    def run(self, *, digest_date: date | None = None, resend: bool = False,
            limit: int | None = None, offline_days: int | None = None) -> RunResult:
        cfg = self.cfg
        today = digest_date or date.today()
        stats = RunStats()
        run_id = None if self.dry_run else self.store.start_run()
        self.log(f"[1/5] 采集：{today.isoformat()}")

        journal_papers = self.collect_journals(today)
        search_papers = self.collect_searches(today)
        advisor_papers_raw, advisor_works = self.collect_advisor(today)

        # 合并去重
        merged: dict[str, Paper] = {}
        for paper in journal_papers + search_papers + advisor_papers_raw:
            if not paper.title or paper.title == "无标题":
                continue
            if paper.key in merged:
                merged[paper.key] = merge_paper(merged[paper.key], paper)
            else:
                merged[paper.key] = paper
        stats.scanned = len(journal_papers) + len(search_papers)
        self.log(f"  去重合并后：{len(merged)} 篇")

        # 一篇都没抓到 = 网络/接口出问题，不能当成"今天没有新文献"静默放过
        if stats.scanned == 0 and not advisor_papers_raw:
            reason = "；".join(self.warnings[:2]) or "所有来源都没有返回数据"
            self.log(f"[×] 采集失败：{reason}")
            if run_id:
                self.store.finish_run(run_id, stats, "failed", message=reason)
            return RunResult(digest_date=today, stats=stats, warnings=self.warnings,
                             failed=True, fail_reason=reason)

        # 匹配 + 打分
        kept: list[Paper] = []
        for paper in merged.values():
            paper.matches = self.matcher.match(paper)
            score_paper(paper, cfg)
            if is_relevant(paper, cfg):
                kept.append(paper)
        stats.matched = len(kept)
        self.explain_rows = sorted(
            [(p.score, p.priority, p.journal, p.title,
              "/".join(f"{m.label}(T{len(m.title_hits)}/A{len(m.abstract_hits)})"
                       for m in p.matches[:3]))
             for p in kept],
            key=lambda row: -row[0],
        )
        self.log(f"[2/5] 方向命中：{len(kept)} 篇")

        # 去重（已推送过的不再推）
        if resend:
            fresh = kept
        else:
            fresh = self.store.filter_new(kept)
        stats.fresh = len(fresh)
        self.log(f"[3/5] 排除已推送后：{len(fresh)} 篇")

        fresh.sort(key=lambda p: p.published or "", reverse=True)
        fresh.sort(key=lambda p: p.score, reverse=True)

        if limit:
            fresh = fresh[:limit]

        # 今天是否已经推过（决定"追加还是全量"）：日记里已有 litpush 块，
        # 或（旧的）独立日报文件已存在。
        journal_settings = None
        journal_note: Path | None = None
        existing_digest = ""
        digest_file = cfg.daily_dir / f"{today.isoformat()}.md"
        if cfg.journal.enabled:
            if not Path(cfg.journal.vault).exists():
                # 模板里是占位路径：别直接报错，退回文件夹模式并提示
                self.warnings.append(
                    f"日记 vault 不存在（{cfg.journal.vault}），本次改为写入 output_dir 的日报文件。"
                    "要启用日记模式，请改 config.toml 的 [journal] vault。")
                journal_mode = False
            else:
                journal_mode = True
        else:
            journal_mode = False

        if journal_mode:
            journal_settings = journal.load_settings(
                cfg.journal.vault,
                folder=cfg.journal.folder,
                date_format=cfg.journal.format,
                template=cfg.journal.template,
                weekday_locale=cfg.journal.weekday_locale,
            )
            journal_note = journal_settings.path_for(today)
            if journal_note.exists():
                try:
                    existing_digest = journal_note.read_text(encoding="utf-8")
                except OSError:
                    existing_digest = ""
            is_batch = journal.BEGIN in existing_digest and not self.dry_run
        else:
            if digest_file.exists():
                try:
                    existing_digest = digest_file.read_text(encoding="utf-8")
                except OSError:
                    existing_digest = ""
            is_batch = bool(existing_digest.strip()) and not self.dry_run

        if is_batch:
            before = len(fresh)
            fresh = [p for p in fresh if p.score >= cfg.scoring.b_threshold]
            if before != len(fresh):
                self.log(f"  今日已推送过：仅保留 B 级以上的 {len(fresh)} 篇"
                         f"（其余 {before - len(fresh)} 篇不追加）")

        detail_cap = cfg.general.max_summaries
        detail = fresh[:detail_cap]
        brief = fresh[detail_cap:]
        stats.pushed = len(detail)
        advisor_set = {p.key for p in advisor_papers_raw}

        # 摘要
        self.log(f"[4/5] 生成摘要：{len(detail)} 篇（{'LLM' if cfg.llm.enabled else '规则'}）")

        # 摘要来源优先级：人工补录 > Crossref 兜底 > OpenAlex 原摘要
        from .overrides import AbstractOverrides
        from .crossref import abstract_by_doi

        overrides = AbstractOverrides(self.data_dir / "abstracts.json")
        manual = sum(1 for paper in detail if overrides.apply(paper))
        enriched = 0
        for paper in detail:
            if paper.abstract or not paper.doi or enriched >= 12:
                continue
            fetched = abstract_by_doi(self.client, paper.doi)
            if fetched:
                paper.abstract = fetched
                enriched += 1
        if manual:
            self.log(f"   人工补录摘要：{manual} 篇")
        if enriched:
            self.log(f"   Crossref 补齐摘要：{enriched} 篇")
        missing = [p for p in detail if not p.abstract]
        if missing:
            self.log(f"   注意：{len(missing)} 篇无摘要（可用 refill --doi 补录）")

        summaries: dict[str, Summary] = {}
        use_llm = cfg.llm.enabled and bool(cfg.llm.api_key())

        def _one(paper: Paper, allow_llm: bool) -> tuple[Paper, Summary, float]:
            started = time.monotonic()
            summary = summarize(paper, cfg)
            if allow_llm:
                polished = llm.polish(paper, summary, cfg)
                if polished:
                    summary = polished
            return paper, summary, time.monotonic() - started

        # 大模型调用是瓶颈（单篇 10–90 s），并发跑；规则版本身很快，串行即可
        if use_llm and detail:
            workers = max(1, min(int(cfg.llm.concurrency), len(detail)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_one, p, True) for p in detail]
                for future in as_completed(futures):
                    paper, summary, cost = future.result()
                    summaries[paper.key] = summary
                    self.log(f"    · [{paper.priority}] {paper.short_title(55)} "
                             f"({paper.score:.1f}) {cost:.0f}s")
        else:
            for paper in detail:
                paper, summary, cost = _one(paper, False)
                summaries[paper.key] = summary
                self.log(f"    · [{paper.priority}] {paper.short_title(55)} ({paper.score:.1f})")

        advisor_papers = [p for p in fresh if p.key in advisor_set] or [
            p for p in kept if p.key in advisor_set]
        advisor_papers.sort(key=lambda p: -p.score)
        for paper in advisor_papers:
            if paper.key not in summaries:
                summaries[paper.key] = summarize(paper, cfg)
        stats.advisor = len(advisor_papers)

        # 写文件
        out_dir = cfg.output_dir
        note_names: dict[str, str] = {}
        note_paths: dict[str, str] = {}
        targets = detail + [p for p in advisor_papers if p.key not in {q.key for q in detail}]

        if self.dry_run:
            for paper in targets:
                note_names[paper.key] = note_filename(paper)
            digest_text = render_daily_note(today, detail, summaries, advisor_papers,
                                            summaries, stats, cfg, note_names, self.warnings,
                                            others=brief[:20])
            if journal_mode and journal_note:
                digest_text = (f"[日记目标] {journal_note}\n" + digest_text)
            self.log(f"[5/5] 预演模式：不会写入 {out_dir}")
            return RunResult(digest_date=today, stats=stats, papers=detail,
                             advisor_papers=advisor_papers, warnings=self.warnings,
                             dry_run=True, preview=digest_text)

        out_dir.mkdir(parents=True, exist_ok=True)
        daily_dir = cfg.daily_dir
        notes_dir = daily_dir / cfg.general.per_paper_subdir

        # 1) 原子卡片（可选；默认关闭，避免每天多出十几个文件）
        if cfg.general.per_paper_notes:
            for paper in targets:
                name = note_filename(paper)
                text = render_paper_note(paper, summaries[paper.key], cfg, today)
                path = write_text(notes_dir / f"{name}.md", text)
                note_names[paper.key] = name
                note_paths[paper.key] = str(path)
                self.notes_paths.append(path)

        # 2) 输出：日记（推荐）或独立日报
        created_note = False
        if journal_mode and journal_note is not None and journal_settings is not None:
            if not detail and not advisor_papers:
                self.log("[5/5] 今日无新增，日记保持原样")
                if run_id:
                    self.store.finish_run(run_id, stats, "ok", message="无新增，未改动日记",
                                          digest_path=str(journal_note))
                return RunResult(digest_date=today, stats=stats, papers=[], advisor_papers=[],
                                 digest_path=journal_note, notes=[], warnings=self.warnings,
                                 preview="")

            if is_batch:
                snippet = "\n".join(render_journal_supplement(today, detail, summaries, cfg, note_names))
                new_text = journal.insert_before_end(existing_digest, snippet)
                journal_note.parent.mkdir(parents=True, exist_ok=True)
                journal_note.write_text(new_text.rstrip() + "\n", encoding="utf-8", newline="\n")
                digest_path = journal_note
            else:
                block = render_journal_block(today, detail, summaries, advisor_papers,
                                             stats, cfg, others=brief[:20],
                                             warnings=self.warnings, note_names=note_names)
                digest_path, created_note = journal.upsert_block(
                    journal_note, block, day=today, settings=journal_settings)
            self.log(f"[5/5] 已写入日记：{digest_path}"
                     + ("（新建了当天日记）" if created_note else ""))
        else:
            if existing_digest.strip() and not detail:
                self.log("[5/5] 今日无新增，保留已有日报")
                if run_id:
                    self.store.finish_run(run_id, stats, "ok", message="无新增，未改动日报",
                                          digest_path=str(digest_file))
                return RunResult(digest_date=today, stats=stats, papers=[], advisor_papers=[],
                                 digest_path=digest_file, notes=[], warnings=self.warnings,
                                 preview="")

            if existing_digest.strip():
                batch = render_daily_note(today, detail, summaries, advisor_papers, summaries,
                                          stats, cfg, note_names, self.warnings, mode="batch")
                digest_text = existing_digest.rstrip() + "\n\n" + batch + "\n"
            else:
                digest_text = render_daily_note(today, detail, summaries, advisor_papers,
                                                summaries, stats, cfg, note_names, self.warnings,
                                                others=brief[:20])
            digest_path = write_text(digest_file, digest_text)
            self.log(f"[5/5] 已写入：{digest_path}")

        self.store.mark(targets, today.isoformat(), run_id, note_paths)
        if brief:
            # 只把真正展示在"其余命中"简表里的记入已推送，避免静默丢弃
            self.store.mark(brief[:20], today.isoformat(), run_id, {})
        if run_id:
            self.store.finish_run(run_id, stats, "ok", digest_path=str(digest_path))

        # 索引最后写：这样"累计推送 / 最近运行"里已经包含本次结果
        write_text(cfg.output_dir / cfg.render.index_note,
                   render_index(cfg, self.store.summary(), self.store.recent_runs(14),
                                stats, today, journal_mode=journal_mode))
        return RunResult(digest_date=today, stats=stats, papers=detail,
                         advisor_papers=advisor_papers, digest_path=digest_path,
                         notes=self.notes_paths, warnings=self.warnings,
                         preview="")

    def close(self) -> None:
        self.store.close()


def run_once(cfg: Config, *, digest_date: date | None = None, resend: bool = False,
             limit: int | None = None, dry_run: bool = False, log=print) -> RunResult:
    pipeline = Pipeline(cfg, log=log, dry_run=dry_run)
    try:
        return pipeline.run(digest_date=digest_date, resend=resend, limit=limit)
    except Exception:
        traceback.print_exc()
        raise
    finally:
        pipeline.close()
