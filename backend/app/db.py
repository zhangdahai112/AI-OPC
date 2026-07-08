"""Durable state store.

SQLite stands in for Temporal here: it gives us persistent, crash-recoverable
orchestration state. Workflow facts (ticket status, gate results, contract,
human signals) are committed to disk so a restart resumes from the last state
rather than losing the run (PRD NFR-2, arch 3.1). Agent *reasoning* is not
persisted — only the durable facts are, matching the doc's determinism boundary.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Iterable

from .config import DB_PATH

_local = threading.local()
_write_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    repo_url   TEXT DEFAULT '',
    branch     TEXT DEFAULT 'main',
    docs       TEXT DEFAULT '',
    status     TEXT DEFAULT 'ready',
    local_path TEXT DEFAULT '',
    clone_log  TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_members (
    channel_id  TEXT NOT NULL,
    role       TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'idle',
    PRIMARY KEY (channel_id, role)
);

CREATE TABLE IF NOT EXISTS channel_projects (
    channel_id  TEXT NOT NULL,
    project_id  TEXT NOT NULL,
    PRIMARY KEY (channel_id, project_id)
);

CREATE TABLE IF NOT EXISTS channel_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,            -- sys|agent|human|card
    role        TEXT,                     -- agent role
    payload     TEXT NOT NULL,            -- JSON
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    type        TEXT NOT NULL,            -- bug | feature | incident
    description TEXT DEFAULT '',
    repo        TEXT DEFAULT '',
    project_id  TEXT DEFAULT '',
    source      TEXT NOT NULL,            -- human | reporter
    lane        TEXT,                     -- fast | warroom
    status      TEXT NOT NULL,
    needs       INTEGER DEFAULT 0,
    trusted     INTEGER DEFAULT 1,
    contract    TEXT,
    pipeline    TEXT,
    budget      TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS roster (
    ticket_id TEXT NOT NULL,
    role      TEXT NOT NULL,
    state     TEXT NOT NULL,
    PRIMARY KEY (ticket_id, role)
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    role       TEXT,
    payload    TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS gate_results (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  TEXT NOT NULL,
    gate_id    TEXT NOT NULL,
    status     TEXT NOT NULL,            -- pass|fail|running|pending
    evidence   TEXT,                     -- JSON evidence
    commit_sha TEXT,
    owner_on_fail TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  TEXT,
    kind       TEXT NOT NULL,           -- decision|route|gate|escalation|approval|tool|memory
    actor      TEXT,
    detail     TEXT,                    -- JSON
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_proposals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    ticket_id  TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected
    created_at REAL NOT NULL
);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def init_db() -> None:
    with _write_lock:
        c = conn()
        c.executescript(SCHEMA)
        # lightweight migrations for pre-existing DBs
        cols = {r[1] for r in c.execute("PRAGMA table_info(tickets)").fetchall()}
        if "project_id" not in cols:
            c.execute("ALTER TABLE tickets ADD COLUMN project_id TEXT DEFAULT ''")
        c.commit()


def now() -> float:
    return time.time()


# ---- JSON helpers -------------------------------------------------------
def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def loads(v: str | None, default: Any = None) -> Any:
    if not v:
        return default
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return default


# ---- write helpers ------------------------------------------------------
def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    with _write_lock:
        c = conn()
        cur = c.execute(sql, tuple(params))
        c.commit()
        return cur


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return conn().execute(sql, tuple(params)).fetchall()


def query_one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return conn().execute(sql, tuple(params)).fetchone()


# ---- kv config store ----------------------------------------------------
def kv_get(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM kv WHERE key=?", (key,))
    return loads(row["value"], default) if row else default


def kv_set(key: str, value: Any) -> None:
    execute(
        "INSERT INTO kv(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, dumps(value)),
    )


# ---- audit (FR-11.1: full-trace audit log) ------------------------------
def audit(kind: str, *, ticket_id: str | None = None, actor: str | None = None,
          detail: Any = None) -> None:
    execute(
        "INSERT INTO audit(ticket_id,kind,actor,detail,created_at) VALUES(?,?,?,?,?)",
        (ticket_id, kind, actor, dumps(detail), now()),
    )
