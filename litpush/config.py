"""配置加载：读取 TOML，补默认值，做基本校验。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.toml"
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "config.example.toml"
DATA_DIR = PROJECT_ROOT / "data"


@dataclass
class Journal:
    name: str
    issn: str
    tier: int = 3
    lookback_days: int = 14
    enabled: bool = True
    short: str = ""

    @property
    def label(self) -> str:
        return self.short or self.name


@dataclass
class Topic:
    id: str
    label: str
    weight: float
    patterns: list[str]
    priority: str = "secondary"
    guard: str = ""


@dataclass
class SearchQuery:
    query: str
    days: int = 10


@dataclass
class Advisor:
    """追踪某个作者的被引情况（自己的论文、关注学者、领域关键作者）。"""

    enabled: bool = False
    name: str = ""
    name_en: str = ""
    orcid: str = ""
    openalex_id: str = ""
    lookback_days: int = 21
    refresh_days: int = 7
    bonus: float = 5.0
    keep_offtopic: bool = True

    @property
    def display_name(self) -> str:
        return self.name or "关注学者"


@dataclass
class General:
    output_dir: str
    language: str = "zh"
    days_window: int = 10
    max_summaries: int = 12
    per_paper_notes: bool = True
    per_paper_subdir: str = "Papers"
    digest_detail: str = "compact"
    polite_email: str = "litpush@example.com"
    user_agent: str = "litpush/1.0"
    cache_ttl_hours: float = 8
    request_interval: float = 0.35
    max_retries: int = 5
    include_preprints: bool = True
    openalex_api_key: str = ""


@dataclass
class Scoring:
    a_threshold: float = 9.0
    b_threshold: float = 6.0
    c_threshold: float = 3.5
    tier1_bonus: float = 2.0
    tier2_bonus: float = 1.0
    tier3_bonus: float = 0.3
    preprint_penalty: float = 0.8
    focus_title_bonus: float = 1.5


@dataclass
class LLM:
    enabled: bool = False
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    api_key_env: str = "LITPUSH_LLM_API_KEY"
    timeout: int = 120
    temperature: float = 0.3
    max_papers: int = 12
    concurrency: int = 4

    def api_key(self) -> str:
        """优先读环境变量；读不到再去注册表里找。

        计划任务（Task Scheduler）启动的进程拿到的是登录时的环境块快照，
        之后才新建的用户变量它看不见——所以这里必须有一次注册表兜底，
        否则定时任务会一直静默走"规则版摘要"。
        """
        value = os.environ.get(self.api_key_env, "").strip()
        if value:
            return value
        if os.name != "nt":
            return ""
        try:
            import winreg

            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, "Environment") as key:
                        raw, _ = winreg.QueryValueEx(key, self.api_key_env)
                        if str(raw).strip():
                            return str(raw).strip()
                except OSError:
                    continue
        except Exception:  # noqa: BLE001 - 读不到就当没配置
            return ""
        return ""


@dataclass
class JournalTarget:
    """把速递写进 Obsidian 日记，而不是另建文件。"""

    enabled: bool = False
    vault: str = ""             # 例：r"C:/Users/YourName/Documents/Obsidian/MyVault"
    folder: str = ""            # 留空 = 读 .obsidian/daily-notes.json
    format: str = ""            # 同上
    template: str = ""          # 同上
    weekday_locale: str = "zh"
    section_title: str = "📚 今日文献速递"


@dataclass
class Render:
    daily_dir: str = ""
    index_note: str = "文献推送总览.md"
    tags: list[str] = field(default_factory=lambda: ["文献推送"])
    dataview_hint: bool = True


@dataclass
class Config:
    general: General
    journals: list[Journal]
    topics: list[Topic]
    searches: list[SearchQuery]
    advisor: Advisor
    scoring: Scoring
    llm: LLM
    render: Render
    journal: JournalTarget
    topic_links: dict[str, str]
    path: Path

    @property
    def output_dir(self) -> Path:
        return Path(self.general.output_dir)

    @property
    def daily_dir(self) -> Path:
        sub = self.general.daily_dir if hasattr(self.general, "daily_dir") else ""
        base = self.output_dir
        return base / sub if sub else base


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError(f"配置节 [{key}] 应为表（table）")
    return value


def load(path: str | Path | None = None) -> Config:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"找不到配置文件：{cfg_path}\n"
            "第一次使用请先执行：  litpush.cmd init   （或手动复制 config/config.example.toml 为 config/config.toml）")

    with cfg_path.open("rb") as fh:
        raw = tomllib.load(fh)

    g = _section(raw, "general")
    if not g.get("output_dir"):
        raise ValueError("[general] output_dir 未设置")

    general = General(**{k: v for k, v in g.items() if k in General.__dataclass_fields__})

    journals = [
        Journal(
            name=j["name"],
            issn=j["issn"],
            tier=int(j.get("tier", 3)),
            lookback_days=int(j.get("lookback_days", general.days_window)),
            enabled=bool(j.get("enabled", True)),
            short=j.get("short", ""),
        )
        for j in raw.get("journals", [])
    ]
    if not journals:
        raise ValueError("[[journals]] 至少需要配置一个期刊")

    topics = [
        Topic(
            id=t["id"],
            label=t["label"],
            weight=float(t.get("weight", 1.0)),
            patterns=list(t.get("patterns", [])),
            priority=t.get("priority", "secondary"),
            guard=t.get("guard", ""),
        )
        for t in raw.get("topics", [])
    ]

    searches = [
        SearchQuery(query=s["query"], days=int(s.get("days", 10)))
        for s in raw.get("search_queries", [])
    ]

    advisor = Advisor(**{k: v for k, v in _section(raw, "advisor").items()
                         if k in Advisor.__dataclass_fields__})
    scoring = Scoring(**{k: v for k, v in _section(raw, "scoring").items()
                         if k in Scoring.__dataclass_fields__})
    llm = LLM(**{k: v for k, v in _section(raw, "llm").items()
                 if k in LLM.__dataclass_fields__})
    render = Render(**{k: v for k, v in _section(raw, "render").items()
                       if k in Render.__dataclass_fields__})
    journal = JournalTarget(**{k: v for k, v in _section(raw, "journal").items()
                               if k in JournalTarget.__dataclass_fields__})

    cfg = Config(
        general=general,
        journals=journals,
        topics=topics,
        searches=searches,
        advisor=advisor,
        scoring=scoring,
        llm=llm,
        render=render,
        journal=journal,
        topic_links={k: str(v) for k, v in _section(raw, "topic_links").items()},
        path=cfg_path,
    )
    return cfg


def write_default_config(target: str | Path) -> Path:
    """把内置模板复制到目标位置（首次初始化用）。"""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    source = EXAMPLE_CONFIG if EXAMPLE_CONFIG.exists() else DEFAULT_CONFIG
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target
