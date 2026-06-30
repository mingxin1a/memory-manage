"""中央 SQLite/FTS5 索引: 从所有项目的记忆文件构建, 随时可重建。

索引是缓存, 不是事实源 —— `.md` 文件才是。任何时候都能 rebuild() 重来。
中文检索用 FTS5 trigram tokenizer(子串匹配, 对中英混合友好)。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from . import config as C
from . import store
from .store import Memory

_COLUMNS = [
    "path", "project", "name", "description", "type",
    "status", "scope", "pinned", "confidence", "tags",
    "volatility", "nature",
    "created_at", "last_accessed", "access_count",
    "archived_at", "trashed_at", "derived_from", "superseded_by",
    "reason", "mtime", "tier", "links", "body",
]

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,
    project       TEXT NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT,
    type          TEXT,
    status        TEXT,
    scope         TEXT,
    pinned        INTEGER DEFAULT 0,
    confidence    REAL DEFAULT 0.8,
    tags          TEXT,
    volatility    TEXT DEFAULT 'normal',
    nature        TEXT DEFAULT 'fact',
    created_at    TEXT,
    last_accessed TEXT,
    access_count  INTEGER DEFAULT 0,
    archived_at   TEXT,
    trashed_at    TEXT,
    derived_from  TEXT,
    superseded_by TEXT,
    reason        TEXT,
    mtime         REAL,
    tier          TEXT,
    links         TEXT,
    body          TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_mem_tier    ON memories(tier);
CREATE INDEX IF NOT EXISTS idx_mem_scope   ON memories(scope);
CREATE INDEX IF NOT EXISTS idx_mem_name    ON memories(name);

-- 访问统计独立成表: 召回命中时更新, 不重写 .md 文件; 整库重建也不丢。
CREATE TABLE IF NOT EXISTS access (
    path          TEXT PRIMARY KEY,
    last_accessed TEXT,
    access_count  INTEGER DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    name, description, body,
    content='memories', content_rowid='id',
    tokenize='trigram'
);

-- 触发器: 保持 FTS 与主表同步
CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, name, description, body)
    VALUES (new.id, new.name, new.description, new.body);
END;
CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, name, description, body)
    VALUES ('delete', old.id, old.name, old.description, old.body);
END;
CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, name, description, body)
    VALUES ('delete', old.id, old.name, old.description, old.body);
    INSERT INTO memories_fts(rowid, name, description, body)
    VALUES (new.id, new.name, new.description, new.body);
END;
"""


def connect(check_same_thread: bool = True) -> sqlite3.Connection:
    C.ensure_dirs()
    con = sqlite3.connect(C.INDEX_DB, check_same_thread=check_same_thread)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    _migrate(con)
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """对已存在的旧索引补齐新增列(CREATE TABLE IF NOT EXISTS 不会加列)。"""
    have = {r["name"] for r in con.execute("PRAGMA table_info(memories)")}
    for col, ddl in (("volatility", "TEXT DEFAULT 'normal'"),
                     ("nature", "TEXT DEFAULT 'fact'")):
        if col not in have:
            con.execute(f"ALTER TABLE memories ADD COLUMN {col} {ddl}")
    con.commit()


def _row_values(mem: Memory, tier: str) -> list:
    d = store.to_row(mem, tier)
    d["pinned"] = 1 if mem.pinned else 0
    return [d[c] for c in _COLUMNS]


def rebuild(con: sqlite3.Connection | None = None) -> dict:
    """全量重建索引: 清空后重新扫描三层所有文件。返回统计。"""
    own = con is None
    con = con or connect()
    try:
        con.execute("DELETE FROM memories")
        stats = {"active": 0, "archived": 0, "trash": 0, "errors": []}
        placeholders = ",".join("?" * len(_COLUMNS))
        sql = f"INSERT INTO memories ({','.join(_COLUMNS)}) VALUES ({placeholders})"
        for path, project, tier in store.iter_all_files():
            try:
                mem = store.parse_file(path, project)
                # tier(物理位置)是权威, 同步进 status
                mem.status = tier
                con.execute(sql, _row_values(mem, tier))
                stats[tier] = stats.get(tier, 0) + 1
            except Exception as e:  # 单文件坏不影响整体
                stats["errors"].append(f"{path}: {e}")
        # 把独立的访问统计覆盖回 memories(access 表更权威, 不随文件重写丢失)
        con.execute("""
            UPDATE memories SET
                last_accessed = COALESCE(
                    (SELECT a.last_accessed FROM access a WHERE a.path = memories.path),
                    last_accessed),
                access_count = COALESCE(
                    (SELECT a.access_count FROM access a WHERE a.path = memories.path),
                    access_count)
        """)
        con.commit()
        return stats
    finally:
        if own:
            con.close()


def bump_access(con: sqlite3.Connection, paths: list[str]) -> None:
    """召回命中后更新访问统计(last_accessed=今天, access_count+1)。轻量, 不碰 .md。"""
    from datetime import datetime
    today = datetime.now().date().isoformat()
    for p in paths:
        con.execute(
            "INSERT INTO access(path, last_accessed, access_count) VALUES(?,?,1) "
            "ON CONFLICT(path) DO UPDATE SET last_accessed=?, access_count=access_count+1",
            (p, today, today))
        con.execute(
            "UPDATE memories SET last_accessed=?, "
            "access_count=COALESCE(access_count,0)+1 WHERE path=?", (today, p))
    con.commit()


def sync(con: sqlite3.Connection | None = None) -> dict:
    """增量同步: 只重解析 mtime 变化的文件, 处理新增/删除。比 rebuild 快得多。

    用于 Stop hook —— 收录本次会话新写/改动的记忆, 而不全量重建。
    """
    own = con is None
    con = con or connect()
    try:
        existing = {r["path"]: r["mtime"] for r in
                    con.execute("SELECT path, mtime FROM memories").fetchall()}
        seen: set[str] = set()
        stats = {"added": 0, "updated": 0, "removed": 0, "errors": []}
        placeholders = ",".join("?" * len(_COLUMNS))
        sql = f"INSERT INTO memories ({','.join(_COLUMNS)}) VALUES ({placeholders})"
        for path, project, tier in store.iter_all_files():
            sp = str(path)
            seen.add(sp)
            try:
                mt = path.stat().st_mtime
                old = existing.get(sp)
                if old is not None and int(old) == int(mt):
                    continue  # 未变, 跳过解析
                mem = store.parse_file(path, project)
                mem.status = tier
                con.execute("DELETE FROM memories WHERE path=?", (sp,))
                con.execute(sql, _row_values(mem, tier))
                stats["updated" if old is not None else "added"] += 1
            except Exception as e:
                stats["errors"].append(f"{path}: {e}")
        # 删除磁盘上已不存在的
        for sp in existing:
            if sp not in seen:
                con.execute("DELETE FROM memories WHERE path=?", (sp,))
                stats["removed"] += 1
        # 覆盖访问统计
        con.execute("""
            UPDATE memories SET
                last_accessed = COALESCE(
                    (SELECT a.last_accessed FROM access a WHERE a.path = memories.path),
                    last_accessed),
                access_count = COALESCE(
                    (SELECT a.access_count FROM access a WHERE a.path = memories.path),
                    access_count)
        """)
        con.commit()
        return stats
    finally:
        if own:
            con.close()


def upsert(con: sqlite3.Connection, mem: Memory, tier: str) -> None:
    """插入或更新单条(按 path)。供生命周期操作后增量同步。"""
    con.execute("DELETE FROM memories WHERE path=?", (mem.path,))
    placeholders = ",".join("?" * len(_COLUMNS))
    sql = f"INSERT INTO memories ({','.join(_COLUMNS)}) VALUES ({placeholders})"
    con.execute(sql, _row_values(mem, tier))
    con.commit()


def remove_path(con: sqlite3.Connection, path: str) -> None:
    con.execute("DELETE FROM memories WHERE path=?", (path,))
    con.commit()


def get(con: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM memories WHERE path=?", (path,)).fetchone()


def all_rows(con: sqlite3.Connection, tier: str | None = None) -> list[sqlite3.Row]:
    if tier:
        return con.execute("SELECT * FROM memories WHERE tier=? ORDER BY project, name",
                           (tier,)).fetchall()
    return con.execute("SELECT * FROM memories ORDER BY project, name").fetchall()


def stats(con: sqlite3.Connection) -> dict:
    """总览统计, 供 dashboard。"""
    out: dict = {}
    out["total"] = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    out["by_tier"] = {r[0]: r[1] for r in con.execute(
        "SELECT tier, COUNT(*) FROM memories GROUP BY tier")}
    out["by_project"] = {r[0]: r[1] for r in con.execute(
        "SELECT project, COUNT(*) FROM memories WHERE tier=? GROUP BY project "
        "ORDER BY 2 DESC", (C.STATUS_ACTIVE,))}
    out["by_type"] = {r[0]: r[1] for r in con.execute(
        "SELECT type, COUNT(*) FROM memories WHERE tier=? GROUP BY type",
        (C.STATUS_ACTIVE,))}
    out["by_scope"] = {r[0]: r[1] for r in con.execute(
        "SELECT scope, COUNT(*) FROM memories WHERE tier=? GROUP BY scope",
        (C.STATUS_ACTIVE,))}
    out["by_nature"] = {r[0]: r[1] for r in con.execute(
        "SELECT nature, COUNT(*) FROM memories WHERE tier=? GROUP BY nature",
        (C.STATUS_ACTIVE,))}
    out["by_volatility"] = {r[0]: r[1] for r in con.execute(
        "SELECT volatility, COUNT(*) FROM memories WHERE tier=? GROUP BY volatility",
        (C.STATUS_ACTIVE,))}
    out["pinned"] = con.execute(
        "SELECT COUNT(*) FROM memories WHERE pinned=1 AND tier=?",
        (C.STATUS_ACTIVE,)).fetchone()[0]
    return out
