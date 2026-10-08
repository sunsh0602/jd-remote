"""링크 추가 이력 — 내장 SQLite(/data/history.db, named volume jdr-data).

추가할 때마다 한 줄: 시각, 사용자, 방식(장바구니/즉시), 저장 위치(화면에 보인 값), 링크 목록.
가볍게 유지하려고 최근 MAX_ROWS 건만 남긴다. 쓰기 실패는 링크 추가 자체를 막지 않는다.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing

DB_PATH = os.environ.get("JDR_HISTORY_DB", "/data/history.db")
MAX_ROWS = 1000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS add_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL    NOT NULL,
    user      TEXT,
    autostart INTEGER NOT NULL DEFAULT 0,
    dest      TEXT,
    urls      TEXT    NOT NULL           -- JSON 배열
);
CREATE INDEX IF NOT EXISTS add_history_ts ON add_history(ts DESC);
"""


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=5)
    c.executescript(_SCHEMA)
    return c


def _row(r) -> dict:
    return {"id": r[0], "ts": r[1], "user": r[2], "autostart": bool(r[3]), "dest": r[4], "urls": json.loads(r[5])}


def record(urls: list[str], autostart: bool, dest: str | None, user: str | None) -> int | None:
    try:
        with closing(_conn()) as c, c:
            cur = c.execute("INSERT INTO add_history(ts,user,autostart,dest,urls) VALUES (?,?,?,?,?)",
                            (time.time(), user, 1 if autostart else 0, dest or None, json.dumps(urls, ensure_ascii=False)))
            c.execute("DELETE FROM add_history WHERE id NOT IN (SELECT id FROM add_history ORDER BY id DESC LIMIT ?)", (MAX_ROWS,))
            return cur.lastrowid
    except (sqlite3.Error, OSError):
        return None


def list_(limit: int = 30, before_id: int | None = None, q: str | None = None) -> list[dict]:
    sql, args = "SELECT id,ts,user,autostart,dest,urls FROM add_history WHERE 1=1", []
    if before_id:
        sql += " AND id < ?"; args.append(before_id)
    if q:
        sql += " AND (urls LIKE ? OR dest LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY id DESC LIMIT ?"; args.append(max(1, min(limit, 100)))
    try:
        with closing(_conn()) as c:
            return [_row(r) for r in c.execute(sql, args)]
    except (sqlite3.Error, OSError):
        return []


def get(entry_id: int) -> dict | None:
    with closing(_conn()) as c:
        r = c.execute("SELECT id,ts,user,autostart,dest,urls FROM add_history WHERE id=?", (entry_id,)).fetchone()
        return _row(r) if r else None


def delete(entry_id: int) -> bool:
    with closing(_conn()) as c, c:
        return c.execute("DELETE FROM add_history WHERE id=?", (entry_id,)).rowcount > 0


def clear() -> int:
    with closing(_conn()) as c, c:
        return c.execute("DELETE FROM add_history").rowcount
