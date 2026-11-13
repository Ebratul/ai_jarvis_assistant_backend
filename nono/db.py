"""Operational SQLite store: settings, reminders, local calendar, conversation log."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY, text TEXT NOT NULL, due_at TEXT NOT NULL, repeat TEXT,
    status TEXT NOT NULL DEFAULT 'pending', source TEXT, created_at TEXT NOT NULL, fired_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_rem_due ON reminders(status, due_at);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, start TEXT NOT NULL, end TEXT, location TEXT,
    notes TEXT, google_id TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evt_start ON events(start);
CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, role TEXT, text TEXT, model TEXT, created_at TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._lock = threading.RLock()

    def _q(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _x(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur.rowcount

    # settings ---------------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self._q("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key: str, value: Any) -> None:
        self._x(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # reminders --------------------------------------------------------------
    def add_reminder(self, text: str, due_at: datetime, repeat: str | None = None, source: str = "voice") -> dict[str, Any]:
        rid = uuid.uuid4().hex[:10]
        self._x(
            "INSERT INTO reminders(id,text,due_at,repeat,status,source,created_at) VALUES(?,?,?,?,?,?,?)",
            (rid, text, due_at.astimezone(timezone.utc).isoformat(timespec="seconds"), repeat, "pending", source,
             utcnow().isoformat(timespec="seconds")),
        )
        return self._q("SELECT * FROM reminders WHERE id=?", (rid,))[0]

    def list_reminders(self, status: str | None = "pending", limit: int = 50) -> list[dict[str, Any]]:
        if status:
            return self._q("SELECT * FROM reminders WHERE status=? ORDER BY due_at LIMIT ?", (status, limit))
        return self._q("SELECT * FROM reminders ORDER BY due_at DESC LIMIT ?", (limit,))

    def due_reminders(self, now: datetime) -> list[dict[str, Any]]:
        return self._q(
            "SELECT * FROM reminders WHERE status='pending' AND due_at<=? ORDER BY due_at",
            (now.astimezone(timezone.utc).isoformat(timespec="seconds"),),
        )

    def update_reminder(self, rid: str, **fields: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        self._x(f"UPDATE reminders SET {sets} WHERE id=?", (*fields.values(), rid))

    def cancel_reminder(self, key: str) -> list[dict[str, Any]]:
        rows = self._q(
            "SELECT * FROM reminders WHERE status='pending' AND (id=? OR text LIKE ?)", (key, f"%{key}%")
        )
        for r in rows:
            self.update_reminder(r["id"], status="cancelled")
        return rows

    # local calendar -----------------------------------------------------------
    def add_event(self, title: str, start: datetime, end: datetime | None, location: str = "", notes: str = "",
                  google_id: str | None = None) -> dict[str, Any]:
        eid = uuid.uuid4().hex[:10]
        self._x(
            "INSERT INTO events(id,title,start,end,location,notes,google_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (eid, title, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds") if end else None,
             location, notes, google_id, utcnow().isoformat(timespec="seconds")),
        )
        return self._q("SELECT * FROM events WHERE id=?", (eid,))[0]

    def list_events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self._q(
            "SELECT * FROM events WHERE start>=? AND start<? ORDER BY start",
            (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
        )

    def delete_event(self, eid: str) -> int:
        return self._x("DELETE FROM events WHERE id=?", (eid,))

    # conversation -------------------------------------------------------------
    def log(self, session: str, role: str, text: str, model: str | None = None) -> None:
        self._x(
            "INSERT INTO conversation(session,role,text,model,created_at) VALUES(?,?,?,?,?)",
            (session, role, text, model, utcnow().isoformat(timespec="seconds")),
        )

    def history(self, session: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._q(
            "SELECT role, text FROM conversation WHERE session=? ORDER BY id DESC LIMIT ?", (session, limit)
        )
        return rows[::-1]

    def recent_conversation(self, limit: int = 30) -> list[dict[str, Any]]:
        return self._q("SELECT * FROM conversation ORDER BY id DESC LIMIT ?", (limit,))[::-1]
