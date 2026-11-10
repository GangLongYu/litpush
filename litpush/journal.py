"""把每日文献速递写进 Obsidian 的日记，而不是另生成一个文件。

规则：
- 日记目录/命名格式/模板全部读 Obsidian 自己的 `.obsidian/daily-notes.json`，
  不猜、不硬编码；
- 当天日记不存在时，按模板新建一份（支持 {{date}} / {{title}} 等占位符）；
- 速递内容包在 `<!-- litpush:begin -->` / `<!-- litpush:end -->` 之间：
  已有就整块替换，没有就追加到文末——所以一天跑多次也不会堆出重复段落。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

BEGIN = "<!-- litpush:begin -->"
END = "<!-- litpush:end -->"

WEEKDAYS_ZH = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]

DEFAULT_FORMAT = "YYYY-MM-DD"


@dataclass
class JournalSettings:
    vault: Path
    folder: str
    date_format: str
    template: str
    weekday_locale: str = "zh"

    def path_for(self, day: date) -> Path:
        rel = format_path(self.date_format, day, self.weekday_locale)
        path = self.vault / self.folder / rel
        if path.suffix.lower() != ".md":
            path = Path(str(path) + ".md")
        return path

    def template_path(self) -> Path | None:
        if not self.template:
            return None
        path = self.vault / self.template
        return path if path.exists() else None


def load_settings(vault: str | Path, *, folder: str = "", date_format: str = "",
                  template: str = "", weekday_locale: str = "zh") -> JournalSettings:
    vault = Path(vault)
    cfg_file = vault / ".obsidian" / "daily-notes.json"
    data: dict = {}
    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    return JournalSettings(
        vault=vault,
        folder=folder or data.get("folder") or "Daily",
        date_format=date_format or data.get("format") or DEFAULT_FORMAT,
        template=template if template is not None and template != "" else (data.get("template") or ""),
        weekday_locale=weekday_locale,
    )


def _weekday_name(day: date, locale: str) -> str:
    table = WEEKDAYS_EN if locale.lower().startswith("en") else WEEKDAYS_ZH
    return table[day.weekday()]


def format_path(pattern: str, day: date, weekday_locale: str = "zh") -> str:
    """把 Obsidian 的日期格式（Moment.js token）转成相对路径。

    只实现日记场景常用的 token：YYYY YY MM M DD D dddd ddd MMMM MMM。
    未知 token 原样保留。
    """
    tokens = {
        "YYYY": f"{day.year:04d}",
        "YY": f"{day.year % 100:02d}",
        "MMMM": MONTHS_EN[day.month - 1],
        "MMM": MONTHS_EN[day.month - 1][:3],
        "MM": f"{day.month:02d}",
        "M": str(day.month),
        "DD": f"{day.day:02d}",
        "D": str(day.day),
        "dddd": _weekday_name(day, weekday_locale),
        "ddd": _weekday_name(day, weekday_locale)[:3],
    }
    pattern = re.sub(r"\[([^\]]*)\]", lambda m: m.group(1), pattern)   # [literal]
    for key in ("YYYY", "MMMM", "dddd", "MMM", "ddd", "YY", "MM", "DD", "M", "D"):
        pattern = pattern.replace(key, tokens[key])
    return pattern


def render_template(text: str, day: date, weekday_locale: str = "zh") -> str:
    """替换模板里的 {{date}} / {{time}} / {{title}} / {{date:FORMAT}}。"""
    title = format_path("YYYY-MM-DD", day, weekday_locale)
    now = datetime.now()

    def repl(match: re.Match) -> str:
        key = match.group(1).strip()
        if key.startswith("date:"):
            return format_path(key[5:].strip(), day, weekday_locale)
        if key == "date":
            return day.isoformat()
        if key == "time":
            return now.strftime("%H:%M")
        if key == "title":
            return title
        return match.group(0)

    return re.sub(r"\{\{([^}]+)\}\}", repl, text)


def insert_before_end(text: str, snippet: str) -> str:
    """把 snippet 插到 <!-- litpush:end --> 之前。"""
    if END not in text:
        return text.rstrip() + "\n\n" + snippet + "\n"
    head, tail = text.split(END, 1)
    return head.rstrip() + "\n\n" + snippet.rstrip() + "\n\n" + END + tail


def upsert_block(note: Path, block: str, *, day: date,
                 settings: JournalSettings) -> tuple[Path, bool]:
    """把 block 写入当天日记（替换旧块或追加到末尾）。返回 (路径, 是否新建)。"""
    created = False
    if note.exists():
        try:
            text = note.read_text(encoding="utf-8")
        except OSError:
            text = ""
    else:
        template = settings.template_path()
        text = render_template(template.read_text(encoding="utf-8"),
                               day, settings.weekday_locale) if template else ""
        created = True

    if BEGIN in text and END in text:
        head = text.split(BEGIN, 1)[0].rstrip()
        tail = text.split(END, 1)[1].lstrip("\n")
        new_text = f"{head}\n\n{block}"
        if tail.strip():
            new_text += "\n\n" + tail
    else:
        new_text = text.rstrip() + ("\n\n" if text.strip() else "") + block

    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(new_text.rstrip() + "\n", encoding="utf-8", newline="\n")
    return note, created
