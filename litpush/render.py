"""Markdown 渲染：单篇精读卡片 / 每日速递 / 总览索引。

刻意使用固定结构 + callout，方便在 Obsidian 里阅读、双链与 Dataview 检索。
"""

from __future__ import annotations

import html
import json
from datetime import date, datetime
from pathlib import Path

from .config import Config
from .model import Paper
from .summarize import Summary
from .store import RunStats
from .textutil import split_sentences

PRIORITY_LABEL = {
    "A": "建议精读",
    "B": "建议选读",
    "C": "存档备查",
    "D": "低相关",
}
PRIORITY_ICON = {"A": "⭐⭐⭐", "B": "⭐⭐", "C": "⭐", "D": "·"}

TIER_LABEL = {1: "核心期刊", 2: "领域期刊", 3: "扩展期刊"}


def yaml_str(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def yaml_list(values: list[str]) -> str:
    return "[" + ", ".join(yaml_str(v) for v in values) + "]"


def _clean_bullet(text: str) -> str:
    return " ".join(text.split())


def _harvest(paper: Paper) -> str:
    if paper.pdf_url:
        return f"[PDF]({paper.pdf_url})"
    return ""


def paper_tags(paper: Paper, cfg: Config) -> list[str]:
    tags = list(cfg.render.tags)
    for label in paper.topic_labels[:3]:
        tags.append(label.replace(" ", "-"))
    if paper.has_advisor_cite:
        tags.append("关注学者引用")
    return tags


def _topic_links(paper: Paper, cfg: Config) -> str:
    links = []
    for match in paper.matches[:3]:
        name = cfg.topic_links.get(match.topic_id)
        if name:
            links.append(f"[[{name}]]")
        else:
            links.append(match.label)
    return " · ".join(links) if links else "—"


def _paper_frontmatter(paper: Paper, cfg: Config, digest_date: date) -> list[str]:
    tags = paper_tags(paper, cfg)
    return [
        "---",
        f"title: {yaml_str(paper.title)}",
        f"aliases: {yaml_list([paper.short_title(60)])}",
        f"authors: {yaml_list(paper.authors[:20])}",
        f"journal: {yaml_str(paper.journal)}",
        f"year: {paper.year or ''}",
        f"published: {paper.published[:10]}",
        f"doi: {yaml_str(paper.doi)}",
        f"url: {yaml_str(paper.url)}",
        f"openalex: {yaml_str(paper.oa_id)}",
        f"type: {paper.type}",
        f"tier: {paper.tier}",
        f"score: {paper.score}",
        f"priority: {paper.priority}",
        f"topics: {yaml_list(paper.topic_labels[:4])}",
        f"tags: {yaml_list(tags)}",
        "status: 未读",
        f"added: {digest_date.isoformat()}",
        f"advisor_cited: {'true' if paper.has_advisor_cite else 'false'}",
        "---",
    ]


def _meta_table(paper: Paper, cfg: Config) -> list[str]:
    tags = " ".join(f"`#{t}`" for t in paper_tags(paper, cfg))
    attach = f"[PDF]({paper.pdf_url})" if paper.pdf_url else "无附件"
    authors = ", ".join(paper.authors[:12])
    if len(paper.authors) > 12:
        authors += " et al."
    rows = [
        ("Title", paper.title),
        ("Journal", paper.journal),
        ("Authors", authors or "未知"),
        ("Pub. date", paper.published[:10] or str(paper.year or "")),
        ("Pub. Tags", tags),
        ("DOI", f"[{paper.doi}]({paper.url})" if paper.doi else f"[链接]({paper.url})"),
        ("Attachment", attach),
    ]
    lines = ["| 字段 | 内容 |", "| --- | --- |"]
    for key, value in rows:
        cell = str(value).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| **{key}** | {cell} |")
    return lines


def _bullets(items: list[str], empty: str = "（摘要未给出，需读原文）") -> list[str]:
    items = [_clean_bullet(i) for i in items if i]
    return [f"- {i}" for i in items] if items else [f"- {empty}"]


def _quote(lines: list[str]) -> list[str]:
    return [f"> {line}" if line else ">" for line in lines]


def render_paper_note(paper: Paper, summary: Summary | None, cfg: Config,
                      digest_date: date) -> str:
    """精简卡片：Meta Data + 研究背景 + 摘要全文 + 引用与关联。

    只处理摘要级别的信息时，堆十几个小标题反而干扰阅读；把摘要原文摆出来，
    需要精读的人自己点 DOI。`summary` 保留在签名里只为兼容调用方（不再使用）。
    """
    lines = _paper_frontmatter(paper, cfg, digest_date)
    lines += [
        "",
        f'<h1 style="text-align: center">{html.escape(paper.title)}</h1>',
        "<hr/>",
        "",
        "## 💡 Meta Data",
        "",
    ]
    lines += _meta_table(paper, cfg)

    lines += ["", "<hr/>", "", "## 📜 研究背景 & 基础 & 目的", ""]
    context: list[str] = []
    if paper.matches:
        context.append("**与你的方向**：" + "；".join(m.label for m in paper.matches[:3]))
    context.append(f"**期刊/时间**：{paper.journal} · {paper.published[:10]}")
    if paper.keywords:
        context.append("**OpenAlex 关键词**：" + "、".join(paper.keywords[:6]))
    lines += [f"- {item}" for item in context]

    lines += ["", "<hr/>", "", "## 📄 摘要全文", ""]
    if paper.abstract:
        lines += [paper.abstract.strip(), ""]
    else:
        lines += ["*出版社没有提供摘要。补录方法：*",
                  "",
                  "```powershell",
                  f".\\litpush.cmd refill --doi {paper.doi or '<DOI>'} --abstract-file 摘要.txt",
                  "```",
                  ""]

    lines += ["<hr/>", "", "## 🔗 引用与关联", ""]
    lines.append(f"- OpenAlex 被引：**{paper.cited_by}** 次")
    lines.append("- 引用关注学者工作：" + ("**是** — " + "；".join(c.label() for c in paper.advisor_cites)
                                     if paper.has_advisor_cite else "否"))
    if paper.doi:
        lines.append(f"- DOI：[{paper.doi}]({paper.url})")
    if paper.pdf_url:
        lines.append(f"- 全文 PDF：[打开]({paper.pdf_url})")
    lines += ["", "---", f"*litpush 生成于 {date.today().isoformat()} · "
                          f"匹配方向：{'、'.join(paper.topic_labels) or '—'}*", ""]
    return "\n".join(lines)


def _compact_card(paper: Paper, summary: Summary, idx: int, note_name: str | None) -> list[str]:
    meta = [f"`{paper.priority}`", f"**{paper.journal}**"]
    if paper.year:
        meta.append(str(paper.year))
    meta.append(f"评分 {paper.score:.1f}")
    if summary.methods_line:
        meta.append(summary.methods_line if len(summary.methods_line) <= 36
                    else summary.methods_line[:36] + "…")
    lines = [
        f"### {idx}. {paper.short_title(80)}",
        "",
        " · ".join(meta),
        "",
        f"> {_clean_bullet(summary.one_liner)}",
        "",
    ]
    picks: list[tuple[str, str]] = []
    if summary.highlights:
        picks.append(("特色", summary.highlights[0]))
    if summary.relation:
        picks.append(("与你", summary.relation[0]))
    if summary.why_read:
        picks.append(("值得读", summary.why_read[0]))
    if summary.setup_line:
        picks.append(("构型/工况", summary.setup_line))
    elif len(summary.results) > 1:
        picks.append(("另见", summary.results[1][1]))
    for label, text in picks:
        lines.append(f"- **{label}**：{_clean_bullet(text)}")
    lines.append("")
    tail = []
    if note_name:
        tail.append(f"[[{note_name}|📄 精读卡片]]")
    if paper.url:
        tail.append(f"[原文]({paper.url})")
    if paper.pdf_url:
        tail.append(f"[PDF]({paper.pdf_url})")
    if tail:
        lines += [" · ".join(tail), ""]
    return lines


def render_daily_note(
    digest_date: date,
    papers: list[Paper],
    summaries: dict[str, Summary],
    advisor_papers: list[Paper],
    advisor_desc: dict[str, Summary],
    stats: RunStats,
    cfg: Config,
    note_names: dict[str, str],
    warnings: list[str],
    others: list[Paper] | None = None,
    mode: str = "full",
) -> str:
    groups = {"A": [], "B": [], "C": [], "D": []}
    for paper in papers:
        groups.setdefault(paper.priority, []).append(paper)

    counts = {k: len(v) for k, v in groups.items()}
    if mode == "batch":
        # 同一天第二次运行时追加的"补充批次"：不带 frontmatter，也不重复概览
        lines = [
            "---",
            "",
            f"## 🆕 补充批次 · {datetime.now().strftime('%H:%M')}",
            "",
            f"> 本次又命中 {len(papers)} 篇（A {counts.get('A', 0)} · B {counts.get('B', 0)}）"
            f"，扫描 {stats.scanned} 篇。",
            "",
        ]
        for key in ("A", "B"):
            for idx, paper in enumerate(groups.get(key, []), start=1):
                lines += _compact_card(paper, summaries[paper.key], idx, note_names.get(paper.key))
        rest = groups.get("C", []) + groups.get("D", [])
        if rest:
            lines += ["**其余：** " + "；".join(p.short_title(45) for p in rest[:8]), ""]
        return "\n".join(lines)

    lines = [
        "---",
        f"title: {yaml_str(f'文献速递 {digest_date.isoformat()}')}",
        f"date: {digest_date.isoformat()}",
        "type: literature-digest",
        f"tags: {yaml_list(list(cfg.render.tags) + ['每日速递'])}",
        f"scanned: {stats.scanned}",
        f"matched: {stats.matched}",
        f"new: {stats.pushed}",
        f"a_count: {counts.get('A', 0)}",
        f"b_count: {counts.get('B', 0)}",
        f"c_count: {counts.get('C', 0)}",
        f"advisor_cited: {len(advisor_papers)}",
        "---",
        "",
        f"# 📚 文献速递 · {digest_date.isoformat()}",
        "",
        "> [!abstract] 今日概览",
        f"> 扫描 **{stats.scanned}** 篇（{len(cfg.journals)} 个期刊 + {len(cfg.searches)} 组主题检索），"
        f"方向命中 **{stats.matched}** 篇，去重后新增 **{stats.pushed}** 篇。",
        f"> A 级 {counts.get('A', 0)} 篇 · B 级 {counts.get('B', 0)} 篇 · C 级 {counts.get('C', 0)} 篇"
        + (f" · 引用关注学者工作 {len(advisor_papers)} 篇" if advisor_papers else ""),
        "",
    ]
    if warnings:
        lines += ["> [!warning] 本次运行提示", *[f"> - {w}" for w in warnings], ""]

    if not papers and not advisor_papers:
        lines += [
            "今天没有符合条件的新文献。可能原因：周末/假期期刊更新少、或你已读过并推送过。",
            "",
            f"*下次运行会自动回溯最近若干天，漏掉的文章不会丢。*",
            "",
        ]
        return "\n".join(lines)

    if groups.get("A"):
        lines += ["## ⭐ A 级 · 建议精读", ""]
        for idx, paper in enumerate(groups["A"], start=1):
            lines += _compact_card(paper, summaries[paper.key], idx, note_names.get(paper.key))
    if groups.get("B"):
        lines += ["## 📌 B 级 · 建议选读", ""]
        for idx, paper in enumerate(groups["B"], start=1):
            lines += _compact_card(paper, summaries[paper.key], idx, note_names.get(paper.key))

    others = (others or []) + groups.get("C", []) + groups.get("D", [])
    if others:
        shown = others[:20]
        lines += ["## 🗂 其余命中（简表）", "",
                  "| # | 标题 | 期刊 | 评分 | 方向 |", "| --- | --- | --- | --- | --- |"]
        for idx, paper in enumerate(shown, start=1):
            title = paper.short_title(70).replace("|", "\\|")
            link = f"[[{note_names.get(paper.key)}|{title}]]" if note_names.get(paper.key) else title
            topics = "、".join(paper.topic_labels[:2]) or ("引用关注学者（方向较弱）" if paper.has_advisor_cite else "—")
            lines.append(f"| {idx} | {link} | {paper.journal} | {paper.score:.1f} | {topics} |")
        if len(others) > len(shown):
            lines.append("")
            lines.append(f"另有 {len(others) - len(shown)} 篇相关度较低的命中未列出（已记入数据库，不重复推送）。")
        lines.append("")

    if advisor_papers:
        lines += [f"## 🔔 引用{cfg.advisor.display_name}工作的新文献", "",
                  f"> 这些文章引用了{cfg.advisor.display_name}的工作，说明它们正在接续/检验已有结论，往往值得优先看。", ""]
        for idx, paper in enumerate(advisor_papers, start=1):
            cited = "；".join(c.label() for c in paper.advisor_cites)
            summary = advisor_desc.get(paper.key) or summaries.get(paper.key)
            head = f"**{paper.title}**"
            if paper.key in note_names:
                head = f"[[{note_names[paper.key]}|{paper.short_title(80)}]]"
            lines += [
                f"### {idx}. {head}",
                "",
                f"- **期刊/时间**：{paper.journal} · {paper.published[:10]}",
                f"- **引用了**：{cited or '关注学者的相关工作'}",
                f"- **方向匹配**：{'、'.join(paper.topic_labels) or '较弱（纯引用追踪）'}",
            ]
            if not paper.abstract:
                lines.append("- **要点**：OpenAlex/Crossref 均无摘要；建议直接看正文引用关注学者工作的位置。")
            elif summary:
                lines.append(f"- **要点**：{_clean_bullet(summary.results[0][1]) if summary.results else _clean_bullet(summary.one_liner)}")
            if paper.url:
                lines.append(f"- **原文**：[{paper.doi or '链接'}]({paper.url})")
            lines.append("")

    lines += ["## 📊 本次运行", "",
              f"- 期刊扫描：{len(cfg.journals)} 个（回溯窗口见配置）",
              f"- 主题检索：{len(cfg.searches)} 组",
              f"- 命中方向：{stats.matched} 篇；新增推送：{stats.pushed} 篇",
              "",
              f"*上一篇/下一篇：见 [[{cfg.render.index_note.replace('.md', '')}|文献推送总览]]*", ""]
    return "\n".join(lines)


def render_index(
    cfg: Config,
    store_summary: dict,
    recent_runs: list,
    stats: RunStats,
    digest_date: date,
    journal_mode: bool = False,
) -> str:
    total = store_summary.get("papers", 0)
    by_priority = store_summary.get("by_priority", {})
    lines = [
        "---",
        "title: 文献推送总览",
        f"tags: {yaml_list(list(cfg.render.tags) + ['索引'])}",
        "type: literature-index",
        f"updated: {digest_date.isoformat()}",
        "---",
        "",
        "# 📚 文献推送总览",
        "",
        "> [!info] 这个库在做什么",
        "> 每天自动扫描你配置的期刊 + 主题检索 + 关注学者引用，"
        "按你配置的研究方向打分，生成中文速读卡片。",
        "",
        f"- 累计推送：**{total}** 篇"
        + (f"（A {by_priority.get('A', 0)} · B {by_priority.get('B', 0)} · C {by_priority.get('C', 0)}）" if total else ""),
        f"- 最近一次运行：{digest_date.isoformat()}，新增 {stats.pushed} 篇",
        "",
        "## 🗓 最近运行",
        "",
        f"| 日期 | 扫描 | 命中 | 新增 | 状态 | {'日记' if journal_mode else '日报'} |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for run in recent_runs[:14]:
        day = (run["started"] or "")[:10]
        digest = (run["digest_path"] or "").rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        digest = digest[:-3] if digest.endswith(".md") else digest
        link = f"[[{digest}]]" if digest else "—"
        lines.append(
            f"| {day} | {run['scanned'] or 0} | {run['matched'] or 0} | "
            f"{run['pushed'] or 0} | {run['status']} | {link} |"
        )

    if journal_mode:
        lines += [
            "",
            "## 📍 速递在哪",
            "",
            "- **每日速递**（速览 + 每篇要点 + 跳转链接）：当天日记末尾的 `## 📚 今日文献速递`。",
            "- **每篇的完整文字卡片**：`output_dir` 下，"
            "文件名是 `年份 第一作者 - 标题`。",
            "",
            "## 🔍 卡片检索（需 Dataview 插件）",
            "",
            "```dataview",
            "TABLE journal AS 期刊, published AS 发表, score AS 评分, priority AS 级别",
            "FROM #文献推送",
            'WHERE status = "未读"',
            "SORT score DESC",
            "LIMIT 40",
            "```",
            "",
            "```dataview",
            "TABLE journal AS 期刊, score AS 评分, advisor_cited AS 引用关注学者",
            "FROM #关注学者引用",
            "SORT published DESC",
            "LIMIT 20",
            "```",
            "",
            "## 🧭 使用提示",
            "",
            "1. 早上先看当天日记的速递，觉得值得深读就点卡片末尾的链接进完整卡片。",
            "2. 卡片里 `status: 未读` 改成 `已读` / `精读` / `略过`，上面的查询会自动过滤。",
            "3. 觉得某一类总是误报：`config/config.toml` 里调 `[[topics]]` 的 `weight` 或删 `patterns`。",
            "",
        ]
        return "\n".join(lines)

    lines += [
        "",
        "## 🔍 常用检索",
        "",
        "按标签浏览：`#文献推送` 全部；`#关注学者引用` 引用关注学者工作的文章。",
        "",
        "如果装了 Dataview 插件，可以用下面的查询（未读优先、按分数排序）：",
        "",
        "```dataview",
        "TABLE journal AS 期刊, published AS 发表, score AS 评分, priority AS 级别",
        "FROM #文献推送",
        'WHERE status = "未读"',
        "SORT score DESC",
        "LIMIT 40",
        "```",
        "",
        "```dataview",
        "TABLE journal AS 期刊, score AS 评分, advisor_cited AS 引用关注学者",
        "FROM #关注学者引用",
        "SORT published DESC",
        "LIMIT 20",
        "```",
        "",
        "## 🧭 使用提示",
        "",
        "1. 每天打开当天的日报，先扫 A 级，再看「引用关注学者工作」一节。",
        "2. 卡片里的 `status: 未读` 可以改成 `已读` / `精读` / `略过`，上面的 Dataview 会自动过滤。",
        "3. 觉得某一类总是误报，去 `config/config.toml` 调 `[[topics]]` 的 `weight` 或删掉对应 `patterns`。",
        "",
    ]
    return "\n".join(lines)


def note_filename(paper: Paper) -> str:
    return paper.slug()


def _demote(lines: list[str], levels: int = 2) -> list[str]:
    prefix = "#" * levels
    return [prefix + line if line.startswith("#") else line for line in lines]


def render_journal_supplement(digest_date: date, papers: list[Paper],
                              summaries: dict[str, Summary], cfg: Config,
                              note_names: dict[str, str] | None = None) -> list[str]:
    """当天已有速递块时，把新一批（B 级以上）追加成补充小节。"""
    note_names = note_names or {}
    lines = [f"### 🆕 补充批次 · {datetime.now().strftime('%H:%M')}", ""]
    for idx, paper in enumerate(papers, start=1):
        lines += _compact_card(paper, summaries[paper.key], idx, note_names.get(paper.key))
    return lines


def render_journal_block(
    digest_date: date,
    papers: list[Paper],
    summaries: dict[str, Summary],
    advisor_papers: list[Paper],
    stats: RunStats,
    cfg: Config,
    others: list[Paper] | None = None,
    warnings: list[str] | None = None,
    created_new_note: bool = False,
    note_names: dict[str, str] | None = None,
) -> str:
    """日记末尾的速递块（带 litpush:begin/end 标记，可整块替换）。"""
    from .journal import BEGIN, END

    note_names = note_names or {}
    groups = {"A": [], "B": [], "C": [], "D": []}
    for paper in papers:
        groups.setdefault(paper.priority, []).append(paper)
    counts = {k: len(v) for k, v in groups.items()}
    title = cfg.journal.section_title or "📚 今日文献速递"

    lines = [
        BEGIN,
        f"## {title} · {digest_date.strftime('%m-%d')}",
        "",
        "> [!abstract] 今日概览",
        f"> 扫描 **{stats.scanned}** 篇（{len(cfg.journals)} 刊 + {len(cfg.searches)} 组主题检索），"
        f"方向命中 **{stats.matched}** 篇，本次推送 **{stats.pushed}** 篇"
        f"（A {counts.get('A', 0)} · B {counts.get('B', 0)}）。",
    ]
    if advisor_papers:
        lines.append(f"> 另有 **{len(advisor_papers)}** 篇引用了{cfg.advisor.display_name}的工作，见文末。")
    if note_names:
        lines.append("> 每篇的完整文字卡片在 `output_dir` 下，点卡片末尾的链接直达。")
    lines.append("")

    if warnings:
        lines += ["> [!warning] 运行提示", *[f"> - {w}" for w in warnings], ""]

    if not papers and not advisor_papers:
        lines += ["今天没有符合条件的新文献；窗口会滚动回溯，漏不掉。", ""]

    for key, heading in (("A", "### ⭐ A 级 · 建议精读"), ("B", "### 📌 B 级 · 建议选读")):
        group = groups.get(key) or []
        if not group:
            continue
        lines += [heading, ""]
        for idx, paper in enumerate(group, start=1):
            lines += _compact_card(paper, summaries[paper.key], idx, note_names.get(paper.key))

    rest = (others or []) + groups.get("C", []) + groups.get("D", [])
    if rest:
        lines += ["### 🗂 其余命中（简表）", "",
                  "| # | 标题 | 期刊 | 评分 | 方向 |", "| --- | --- | --- | --- | --- |"]
        for idx, paper in enumerate(rest[:20], start=1):
            title_cell = paper.short_title(70).replace("|", "\\|")
            topics = "、".join(paper.topic_labels[:2]) or (
                "引用关注学者（方向较弱）" if paper.has_advisor_cite else "—")
            lines.append(f"| {idx} | {title_cell} | {paper.journal} | {paper.score:.1f} | {topics} |")
        if len(rest) > 20:
            lines.append("")
            lines.append(f"另有 {len(rest) - 20} 篇相关度较低的命中未列出。")
        lines.append("")

    if advisor_papers:
        lines += [f"### 🔔 引用{cfg.advisor.display_name}工作的新文献", ""]
        for idx, paper in enumerate(advisor_papers, start=1):
            cited = "；".join(c.label() for c in paper.advisor_cites)
            lines.append(f"{idx}. **{paper.title}**")
            lines.append(f"   - 期刊/时间：{paper.journal} · {paper.published[:10]}")
            if cited:
                lines.append(f"   - 引用了：{cited}")
            summary = summaries.get(paper.key)
            if paper.abstract and summary and summary.results:
                lines.append(f"   - 要点：{_clean_bullet(summary.results[0][1])}")
            elif not paper.abstract:
                lines.append("   - 要点：无摘要，建议看正文引用关注学者工作的位置")
            if paper.url:
                lines.append(f"   - 原文：[DOI]({paper.url})")
        lines.append("")

    stamp = datetime.now().strftime("%H:%M")
    lines.append(f"*litpush 自动生成 {digest_date.isoformat()} {stamp}"
                 + ("（本篇日记为新建）" if created_new_note else "") + "*")
    lines.append(END)
    return "\n".join(lines)


def write_text(path: Path, text: str, retries: int = 4) -> Path:
    """原子写入，并兼容 OneDrive/坚果云等"云端占位文件"被占用的情况。

    Obsidian 库里常见带有 ReparsePoint 属性的占位文件，直接覆盖会报
    PermissionError；此时删掉占位文件再写即可（内容会重新上传）。
    """
    import os
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".litpush.tmp")
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            tmp.write_text(text, encoding="utf-8", newline="\n")
            os.replace(tmp, path)
            return path
        except PermissionError as exc:
            last_error = exc
            try:
                if path.exists():
                    path.unlink()
                os.replace(tmp, path)
                return path
            except OSError:
                time.sleep(1.0 + attempt)
        except OSError as exc:
            last_error = exc
            time.sleep(0.5)
    if last_error:
        raise last_error
    return path
