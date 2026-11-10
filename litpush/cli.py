"""命令行入口。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config as config_mod
from .config import Config, load
from .http import ApiClient
from .pipeline import Pipeline, run_once
from .store import Store


def _reconfigure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _cfg(args) -> Config:
    return load(getattr(args, "config", None) or None)


def _log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


# --------------------------------------------------------------------- doctor
def cmd_doctor(args) -> int:
    ok = True
    print("== litpush 自检 ==")
    try:
        cfg = _cfg(args)
    except Exception as exc:  # noqa: BLE001
        print(f"[×] 配置加载失败：{exc}")
        return 1
    print(f"[√] 配置文件：{cfg.path}")
    print(f"[√] 期刊：{len([j for j in cfg.journals if j.enabled])} 个；方向：{len(cfg.topics)} 个；"
          f"主题检索：{len(cfg.searches)} 组")

    out = cfg.output_dir
    print(f"[i] 输出目录：{out}")
    if not out.exists() and not out.parent.exists():
        ok = False
        print("[×] 上级目录不存在——这通常是还没改模板里的占位路径")
        print("    请编辑 config/config.toml 的 [general] output_dir，指向你真实的目录")
    else:
        try:
            out.mkdir(parents=True, exist_ok=True)
            probe = out / ".litpush_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            print("[√] 输出目录可写")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"[×] 输出目录不可写：{exc}")

    data_dir = Path(cfg.path).resolve().parent.parent / "data"
    print(f"[i] 数据目录：{data_dir}")
    try:
        store = Store(data_dir / "litpush.sqlite3")
        summary = store.summary()
        print(f"[√] 数据库正常：已推送 {summary['papers']} 篇，运行 {summary['runs']} 次")
        store.close()
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[×] 数据库异常：{exc}")

    client = ApiClient(data_dir / "cache", ttl_hours=0, interval=0.3,
                       user_agent=cfg.general.user_agent,
                       polite_email=cfg.general.polite_email)
    try:
        data = client.get_json("https://api.openalex.org/works",
                               {"per-page": 1, "filter": "publication_year:2026"})
        print(f"[√] OpenAlex 连通，2026 年文献总量 {data['meta']['count']}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[×] OpenAlex 不可达：{exc}")

    if cfg.llm.enabled:
        from . import llm

        healthy, message = llm.health_check(cfg)
        print(("[√] " if healthy else "[×] ") + f"LLM 检查：{message}")
        if healthy:
            ok_models, models = llm.list_models(cfg)
            if ok_models:
                print(f"[i] 该 key 可用模型（{len(models)} 个）：" + "、".join(models[:12]))
                if cfg.llm.model not in models:
                    print(f"[!] 你配置的 model = {cfg.llm.model!r} 不在上面的列表里，"
                          f"请改成列表中的名字")
        else:
            print(f"    配置：base_url = {cfg.llm.base_url}，model = {cfg.llm.model}")
            print("    提示：未配置 key 时会自动回退到规则版摘要，不影响每日运行。")
    else:
        print("[i] LLM 未启用（规则版摘要；可在 config.toml 打开 [llm]）")

    print("\n结论：" + ("一切就绪，可以运行 litpush run" if ok else "存在问题，请先修复上面的 [×] 项"))
    return 0 if ok else 1


# ------------------------------------------------------------------------ run
def check_output_dir(cfg) -> bool:
    """输出目录的上级必须存在，否则多半是还留着模板里的占位路径。"""
    out = cfg.output_dir
    if out.exists():
        return True
    if not out.parent.exists():
        print(f"[×] 输出目录的上级目录不存在：{out.parent}")
        print("    请先修改 config/config.toml 里的 [general] output_dir（模板里是占位路径）")
        return False
    return True


def cmd_run(args) -> int:
    cfg = _cfg(args)
    if not check_output_dir(cfg):
        return 1
    digest_date = date.fromisoformat(args.date) if args.date else date.today()
    if args.days:
        cfg.general.days_window = args.days
    result = run_once(cfg, digest_date=digest_date, resend=args.resend,
                      limit=args.limit, dry_run=False, log=_log)
    print()
    print(f"扫描 {result.stats.scanned} · 命中 {result.stats.matched} · "
          f"新增 {result.stats.pushed} · 引用关注学者 {len(result.advisor_papers)}")
    for warning in result.warnings:
        print(f"  ! {warning}")
    if result.failed:
        print(f"[×] 本次运行失败：{result.fail_reason}")
        return 1
    if result.digest_path:
        print(f"日报：{result.digest_path}")
    return 0


def cmd_preview(args) -> int:
    cfg = _cfg(args)
    if not check_output_dir(cfg):
        return 1
    digest_date = date.fromisoformat(args.date) if args.date else date.today()
    pipeline = Pipeline(cfg, log=_log, dry_run=True)
    try:
        result = pipeline.run(digest_date=digest_date, resend=args.resend,
                              limit=args.limit)
    finally:
        pipeline.close()
    for paper in result.papers:
        print(f"  [{paper.priority}] {paper.score:5.1f}  {paper.journal:38s}  {paper.short_title(70)}")
    if args.explain and getattr(pipeline, "explain_rows", None):
        print("\n== 全部命中评分明细（调参用）==")
        for score, priority, journal, title, topics in pipeline.explain_rows[:60]:
            print(f"{score:6.2f} {priority} {journal[:28]:28s} {title[:60]:60s} {topics[:40]}")
        buckets = {}
        for score, priority, *_ in pipeline.explain_rows:
            key = "A(≥%.0f)" % cfg.scoring.a_threshold if score >= cfg.scoring.a_threshold else (
                "B" if score >= cfg.scoring.b_threshold else (
                    "C" if score >= cfg.scoring.c_threshold else "D"))
            buckets[key] = buckets.get(key, 0) + 1
        print("\n分档统计：", buckets)
    print("\n" + "=" * 78 + "\n")
    print(result.preview or "(无内容)")
    return 0


def cmd_stats(args) -> int:
    cfg = _cfg(args)
    data_dir = Path(cfg.path).resolve().parent.parent / "data"
    store = Store(data_dir / "litpush.sqlite3")
    summary = store.summary()
    print(f"累计推送：{summary['papers']} 篇 {summary['by_priority']}")
    print(f"运行次数：{summary['runs']}")
    print("\n最近运行：")
    print(f"{'时间':20s} {'扫描':>5s} {'命中':>5s} {'新增':>5s}  状态")
    for run in store.recent_runs(15):
        print(f"{(run['started'] or '')[:19]:20s} {run['scanned'] or 0:5d} "
              f"{run['matched'] or 0:5d} {run['pushed'] or 0:5d}  {run['status']}")
    store.close()
    return 0


def cmd_advisor(args) -> int:
    cfg = _cfg(args)
    pipeline = Pipeline(cfg, log=_log, dry_run=True)
    try:
        pipeline.store._conn.execute("DELETE FROM advisor_works")  # noqa: SLF001 - 强制刷新
        pipeline.store._conn.commit()
        works = pipeline._advisor_works()      # noqa: SLF001
        print(f"关注学者著作：{len(works)} 篇（已写入缓存）")
        newest = sorted(works.values(), key=lambda w: (w.get("year") or 0), reverse=True)[:8]
        for work in newest:
            print(f"  {work.get('year')} · 被引 {work.get('cited', 0):4d} · {(work.get('title') or '')[:80]}")
    finally:
        pipeline.close()
    return 0


def cmd_reset(args) -> int:
    cfg = _cfg(args)
    data_dir = Path(cfg.path).resolve().parent.parent / "data"
    store = Store(data_dir / "litpush.sqlite3")
    summary = store.summary()
    if not args.yes:
        answer = input(f"将清空推送记录（{summary['papers']} 篇）与运行历史，确认？[y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("已取消")
            store.close()
            return 1
    store._conn.execute("DELETE FROM papers")   # noqa: SLF001
    store._conn.execute("DELETE FROM runs")     # noqa: SLF001
    store._conn.commit()
    store.close()
    print("已清空推送记录。下次运行会把窗口内的文献重新推一遍。")
    return 0


def cmd_refill(args) -> int:
    """补/重做单篇卡片（典型场景：出版社没给摘要）。"""
    from .refill import Refiller

    cfg = _cfg(args)
    abstract = ""
    if args.abstract_file:
        abstract = Path(args.abstract_file).read_text(encoding="utf-8")
    elif args.abstract:
        abstract = args.abstract

    refiller = Refiller(cfg, log=_log)
    paper = refiller.build(args.doi, abstract)
    print(f"文献：{paper.title}")
    print(f"期刊：{paper.journal} · {paper.published} · 评分 {paper.score:.1f}（{paper.priority} 级）")
    print(f"方向：{'、'.join(paper.topic_labels) or '—'}")
    if not paper.abstract:
        print("！这篇仍然没有摘要：把摘要存成 txt，用 --abstract-file 再跑一次")
        return 1
    summary = refiller.summarize(paper, use_llm=not args.no_llm)
    refiller.write_card(paper, summary, date.today())
    if not args.no_journal:
        refiller.patch_journal(paper, summary, date.today())
    return 0


def cmd_recard(args) -> int:
    """按当前模板重排已有卡片（不调大模型，只重写文件）。"""
    from .refill import Refiller

    cfg = _cfg(args)
    refiller = Refiller(cfg, log=_log)
    done = refiller.rerender_cards(limit=args.limit)
    print(f"已重排 {done} 张卡片（模板：Meta Data + 研究背景 + 摘要全文 + 引用与关联）")
    return 0


def cmd_init(args) -> int:
    target = Path(args.config) if getattr(args, "config", None) else config_mod.DEFAULT_CONFIG
    if target.exists() and not args.force:
        print(f"配置已存在：{target}（加 --force 覆盖）")
        return 1
    config_mod.write_default_config(target)
    print(f"已写入默认配置：{target}")
    return 0


def _powershell(script: str, extra: list[str]) -> int:
    ps = Path(config_mod.PROJECT_ROOT) / "scripts" / script
    if not ps.exists():
        print(f"找不到脚本：{ps}")
        return 1
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)] + extra
    return subprocess.call(cmd)


def cmd_install_task(args) -> int:
    extra = ["-Time", args.time]
    if args.name:
        extra += ["-TaskName", args.name]
    return _powershell("install_task.ps1", extra)


def cmd_uninstall_task(args) -> int:
    extra = []
    if args.name:
        extra += ["-TaskName", args.name]
    return _powershell("uninstall_task.ps1", extra)


# ----------------------------------------------------------------------- main
def _add_config(parser: argparse.ArgumentParser) -> None:
    """让 --config 既能写在子命令前，也能写在子命令后。"""
    parser.add_argument(
        "--config", default=argparse.SUPPRESS,
        help="配置文件路径（默认 config/config.toml）",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="litpush",
        description="学术文献每日推送：扫描核心期刊 + 关注学者引用，生成 Obsidian 中文速读卡片。",
    )
    _add_config(parser)
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="执行一次抓取并写入 Obsidian（默认命令）")
    _add_config(run)
    run.add_argument("--date", help="指定日期 YYYY-MM-DD（补跑某天）")
    run.add_argument("--days", type=int, help="覆盖期刊回溯天数")
    run.add_argument("--limit", type=int, help="只处理分数最高的 N 篇")
    run.add_argument("--resend", action="store_true", help="忽略去重，重新推送")
    run.set_defaults(func=cmd_run)

    preview = sub.add_parser("preview", help="只看结果不写文件（调参用）")
    _add_config(preview)
    preview.add_argument("--date")
    preview.add_argument("--limit", type=int)
    preview.add_argument("--resend", action="store_true")
    preview.add_argument("--explain", action="store_true", help="打印全部命中的评分明细")
    preview.set_defaults(func=cmd_preview)

    doctor = sub.add_parser("doctor", help="自检：配置、目录权限、网络、模型")
    _add_config(doctor)
    doctor.set_defaults(func=cmd_doctor)

    stats = sub.add_parser("stats", help="查看历史运行与推送统计")
    _add_config(stats)
    stats.set_defaults(func=cmd_stats)

    advisor = sub.add_parser("advisor-refresh", help="强制刷新关注学者著作列表")
    _add_config(advisor)
    advisor.set_defaults(func=cmd_advisor)

    init = sub.add_parser("init", help="生成默认配置文件")
    _add_config(init)
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    reset = sub.add_parser("reset", help="清空推送记录（想重新推送时用）")
    _add_config(reset)
    reset.add_argument("--yes", action="store_true", help="跳过确认")
    reset.set_defaults(func=cmd_reset)

    refill = sub.add_parser("refill", help="补/重做某一篇的卡片（缺摘要时用）")
    _add_config(refill)
    refill.add_argument("--doi", required=True, help="文献 DOI")
    refill.add_argument("--abstract-file", help="摘要文本文件（UTF-8）")
    refill.add_argument("--abstract", help="直接给摘要字符串")
    refill.add_argument("--no-llm", action="store_true", help="不用大模型，只跑规则版")
    refill.add_argument("--no-journal", action="store_true", help="不更新日记里的速览")
    refill.set_defaults(func=cmd_refill)

    recard = sub.add_parser("recard", help="按当前模板重排已有卡片（不调大模型）")
    _add_config(recard)
    recard.add_argument("--limit", type=int, default=0, help="只重排前 N 张（默认全部）")
    recard.set_defaults(func=cmd_recard)

    install = sub.add_parser("install-task", help="注册 Windows 计划任务（每天自动跑）")
    _add_config(install)
    install.add_argument("--time", default="08:30", help="每天运行时刻，默认 08:30")
    install.add_argument("--name", default="", help="任务名")
    install.set_defaults(func=cmd_install_task)

    uninstall = sub.add_parser("uninstall-task", help="删除计划任务")
    _add_config(uninstall)
    uninstall.add_argument("--name", default="")
    uninstall.set_defaults(func=cmd_uninstall_task)
    return parser


def main(argv: list[str] | None = None) -> int:
    _reconfigure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args((argv or []) + ["run"] if argv is not None else ["run"])
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
