"""SQLite 状态库：已推送去重、运行记录、关注学者著作缓存。"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from .model import Paper

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    key         TEXT PRIMARY KEY,
    title_key   TEXT,
    doi         TEXT,
    oa_id       TEXT,
    title       TEXT,
    journal     TEXT,
    published   TEXT,
    score       REAL,
    priority    TEXT,
    digest      TEXT,
    note_path   TEXT,
    first_seen  TEXT,
    pushed_at   TEXT,
    run_id      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_papers_title ON papers(title_key);
CREATE INDEX IF NOT EXISTS idx_papers_pushed ON papers(pushed_at);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started     TEXT,
    finished    TEXT,
    scanned     INTEGER,
    matched     INTEGER,
    fresh       INTEGER,
    pushed      INTEGER,
    status      TEXT,
    message     TEXT,
    digest_path TEXT
);

CREATE TABLE IF NOT EXISTS advisor_works (
    id      TEXT PRIMARY KEY,
    title   TEXT,
    year    INTEGER,
    doi     TEXT,
    cited   INTEGER,
    updated TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())[:160]


@dataclass
class RunStats:
    scanned: int = 0
    matched: int = 0
    fresh: int = 0
    pushed: int = 0
    advisor: int = 0


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------- 去重
    def seen(self, paper: Paper) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM papers WHERE key = ? OR (title_key = ? AND title_key <> '') LIMIT 1",
            (paper.key, title_key(paper.title)),
        )
        return cur.fetchone() is not None

    def filter_new(self, papers: Iterable[Paper]) -> list[Paper]:
        out = []
        for paper in papers:
            if not self.seen(paper):
                out.append(paper)
        return out

    def mark(self, papers: Iterable[Paper], digest: str, run_id: int | None,
             note_paths: dict[str, str] | None = None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        note_paths = note_paths or {}
        rows = [
            (
                p.key, title_key(p.title), p.doi, p.oa_id, p.title, p.journal,
                p.published, p.score, p.priority, digest,
                note_paths.get(p.key, ""), now, now, run_id,
            )
            for p in papers
        ]
        self._conn.executemany(
            """INSERT OR REPLACE INTO papers
               (key,title_key,doi,oa_id,title,journal,published,score,priority,
                digest,note_path,first_seen,pushed_at,run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        self._conn.commit()

    # ------------------------------------------------------------- 运行记录
    def start_run(self) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs (started, status) VALUES (?, 'running')",
            (datetime.now().isoformat(timespec="seconds"),),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, stats: RunStats, status: str,
                   message: str = "", digest_path: str = "") -> None:
        self._conn.execute(
            """UPDATE runs SET finished=?, scanned=?, matched=?, fresh=?, pushed=?,
                   status=?, message=?, digest_path=? WHERE id=?""",
            (datetime.now().isoformat(timespec="seconds"), stats.scanned, stats.matched,
             stats.fresh, stats.pushed, status, message[:2000], digest_path, run_id),
        )
        self._conn.commit()

    def recent_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)))

    # ------------------------------------------------------------- 关注学者缓存
    def get_advisor_works(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM advisor_works").fetchall()
        return [dict(row) for row in rows]

    def set_advisor_works(self, works: list[dict]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self._conn.execute("DELETE FROM advisor_works")
        self._conn.executemany(
            "INSERT INTO advisor_works (id,title,year,doi,cited,updated) VALUES (?,?,?,?,?,?)",
            [(w["id"], w.get("title", ""), w.get("year"), w.get("doi", ""),
              w.get("cited", 0), now) for w in works],
        )
        self._conn.commit()

    # ------------------------------------------------------------- KV
    def get_kv(self, key: str) -> dict | None:
        row = self._conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["v"])
        except json.JSONDecodeError:
            return None

    def set_kv(self, key: str, value: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self._conn.commit()

    # ------------------------------------------------------------- 统计
    def summary(self) -> dict:
        total = self._conn.execute("SELECT COUNT(*) AS c FROM papers").fetchone()["c"]
        by_priority = {row["priority"]: row["c"] for row in self._conn.execute(
            "SELECT priority, COUNT(*) AS c FROM papers GROUP BY priority")}
        runs = self._conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
        return {"papers": total, "by_priority": by_priority, "runs": runs}

    def papers_by_digest(self, digest: str) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT * FROM papers WHERE digest=? ORDER BY score DESC", (digest,)))

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
