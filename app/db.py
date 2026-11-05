"""SQLite storage: settings (key/value), recipients and a send log."""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("LASTCALL_DATA", "/data"))
DB_PATH = DATA_DIR / "lastcall.db"

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipients (
    jellyfin_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 0,
    last_sent   TEXT
);
CREATE TABLE IF NOT EXISTS suggestions (
    user_id  TEXT NOT NULL,
    item_key TEXT NOT NULL,
    ts       TEXT NOT NULL,
    PRIMARY KEY (user_id, item_key)
);
CREATE TABLE IF NOT EXISTS send_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    recipient TEXT NOT NULL,
    status    TEXT NOT NULL,
    detail    TEXT NOT NULL DEFAULT ''
);
"""

DEFAULTS = {
    "jellyfin_url": "",
    "jellyfin_api_key": "",
    "jellyfin_public_url": "",
    "maintainerr_url": "",
    "maintainerr_api_key": "",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_security": "starttls",  # none | starttls | ssl
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from_name": "Last Call",
    "smtp_from_email": "",
    "schedule_enabled": "0",
    "schedule_frequency": "weekly",  # daily | weekly | biweekly | monthly
    "schedule_weekday": "fri",
    "schedule_time": "18:00",
    "language": "de",
    "server_name": "Jellyfin",
    "new_lookback_days": "7",
    "reco_enabled": "1",
    "reco_count": "6",
    "rewatch_enabled": "1",
    "rewatch_count": "3",
    "rewatch_min_days": "180",
    "suggestion_cooldown_days": "90",
    "send_suggestions_only": "0",
}

CHECKBOX_KEYS = {"schedule_enabled", "reco_enabled", "rewatch_enabled", "send_suggestions_only"}
NUMERIC_KEYS = {"smtp_port", "new_lookback_days", "reco_count", "rewatch_count",
                "rewatch_min_days", "suggestion_cooldown_days"}

SECRET_KEYS = {"jellyfin_api_key", "maintainerr_api_key", "smtp_password"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)


# --- settings -------------------------------------------------------------

def get_settings() -> dict[str, str]:
    with connect() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    data = dict(DEFAULTS)
    data.update({r["key"]: r["value"] for r in rows})
    return data


def get_setting(key: str, default: str = "") -> str:
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    return DEFAULTS.get(key, default)


def set_settings(values: dict[str, str]) -> None:
    with connect() as c:
        c.executemany(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            list(values.items()),
        )


# --- recipients -----------------------------------------------------------

def list_recipients() -> list[sqlite3.Row]:
    with connect() as c:
        return c.execute("SELECT * FROM recipients ORDER BY name COLLATE NOCASE").fetchall()


def get_recipient(jellyfin_id: str) -> sqlite3.Row | None:
    with connect() as c:
        return c.execute("SELECT * FROM recipients WHERE jellyfin_id = ?", (jellyfin_id,)).fetchone()


def upsert_recipient_name(jellyfin_id: str, name: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO recipients(jellyfin_id, name) VALUES(?, ?) "
            "ON CONFLICT(jellyfin_id) DO UPDATE SET name = excluded.name",
            (jellyfin_id, name),
        )


def update_recipient(jellyfin_id: str, email: str, enabled: bool) -> None:
    with connect() as c:
        c.execute(
            "UPDATE recipients SET email = ?, enabled = ? WHERE jellyfin_id = ?",
            (email, int(enabled), jellyfin_id),
        )


def delete_recipient(jellyfin_id: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM recipients WHERE jellyfin_id = ?", (jellyfin_id,))
        c.execute("DELETE FROM suggestions WHERE user_id = ?", (jellyfin_id,))


def mark_sent(jellyfin_id: str, ts: str) -> None:
    with connect() as c:
        c.execute("UPDATE recipients SET last_sent = ? WHERE jellyfin_id = ?", (ts, jellyfin_id))


# --- suggestion history (avoid repeating recommendations) ---------------

def recent_suggestions(user_id: str, days: int) -> set[str]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with connect() as c:
        rows = c.execute(
            "SELECT item_key FROM suggestions WHERE user_id = ? AND ts >= ?", (user_id, cutoff)
        ).fetchall()
    return {r["item_key"] for r in rows}


def record_suggestions(user_id: str, keys: set[str]) -> None:
    ts = now_iso()
    with connect() as c:
        c.executemany(
            "INSERT INTO suggestions(user_id, item_key, ts) VALUES(?, ?, ?) "
            "ON CONFLICT(user_id, item_key) DO UPDATE SET ts = excluded.ts",
            [(user_id, k, ts) for k in keys],
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(timespec="seconds")
        c.execute("DELETE FROM suggestions WHERE ts < ?", (cutoff,))


# --- log ------------------------------------------------------------------

def log(recipient: str, status: str, detail: str = "") -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO send_log(ts, recipient, status, detail) VALUES(?, ?, ?, ?)",
            (now_iso(), recipient, status, detail[:500]),
        )
        # keep the log small
        c.execute("DELETE FROM send_log WHERE id NOT IN (SELECT id FROM send_log ORDER BY id DESC LIMIT 500)")


def recent_log(limit: int = 50) -> list[sqlite3.Row]:
    with connect() as c:
        return c.execute("SELECT * FROM send_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
