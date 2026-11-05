"""SQLite storage: settings (key/value), people (recipients), suggestion history and a send log."""

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
-- one row per person; a person can have a Jellyfin account, a Plex account or both
CREATE TABLE IF NOT EXISTS people (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 0,
    servers     TEXT NOT NULL DEFAULT 'both',   -- both (one combined mail) | jellyfin | plex
    jellyfin_id TEXT UNIQUE,
    plex_id     TEXT UNIQUE,
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
    "jellyfin_enabled": "1",
    "maintainerr_url": "",
    "maintainerr_api_key": "",
    "plex_enabled": "0",
    "plex_url": "",
    "plex_token": "",
    "plex_public_url": "",
    "plex_server_name": "",
    "plex_maintainerr_url": "",
    "plex_maintainerr_api_key": "",
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

CHECKBOX_KEYS = {"jellyfin_enabled", "plex_enabled", "schedule_enabled", "reco_enabled", "rewatch_enabled", "send_suggestions_only"}
NUMERIC_KEYS = {"smtp_port", "new_lookback_days", "reco_count", "rewatch_count",
                "rewatch_min_days", "suggestion_cooldown_days"}

SECRET_KEYS = {"jellyfin_api_key", "maintainerr_api_key", "smtp_password", "plex_token", "plex_maintainerr_api_key"}
SERVER_CHOICES = ("both", "jellyfin", "plex")


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
        _migrate_v1_recipients(c)


def _migrate_v1_recipients(c: sqlite3.Connection) -> None:
    """v1.x stored Jellyfin users in `recipients`; move them to `people`."""
    old = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='recipients'").fetchone()
    if not old:
        return
    for r in c.execute("SELECT * FROM recipients").fetchall():
        cur = c.execute(
            "INSERT OR IGNORE INTO people(name, email, enabled, servers, jellyfin_id, last_sent) "
            "VALUES(?, ?, ?, 'both', ?, ?)",
            (r["name"], r["email"], r["enabled"], r["jellyfin_id"], r["last_sent"]),
        )
        if cur.rowcount:
            c.execute("UPDATE suggestions SET user_id = ? WHERE user_id = ?", (str(cur.lastrowid), r["jellyfin_id"]))
    c.execute("ALTER TABLE recipients RENAME TO recipients_v1_backup")


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


# --- people (recipients) -------------------------------------------------

ACCOUNT_COLUMNS = {"jellyfin": "jellyfin_id", "plex": "plex_id"}


def list_people() -> list[sqlite3.Row]:
    with connect() as c:
        return c.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE").fetchall()


def get_person(person_id: int | str) -> sqlite3.Row | None:
    with connect() as c:
        return c.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()


def sync_accounts(server: str, accounts: list[dict]) -> tuple[int, int, int]:
    """Merge the user list of one media server into `people`.

    Existing links are kept. An unlinked account is attached to a person with
    the same name (or e-mail) that has no account on this server yet, so
    someone using Jellyfin *and* Plex ends up as one person; otherwise a new,
    disabled person is created. Accounts that disappeared are unlinked, and
    people without any account left are removed.
    Returns (added, linked, removed)."""
    col = ACCOUNT_COLUMNS[server]
    added = linked = removed = 0
    with connect() as c:
        people = c.execute("SELECT * FROM people").fetchall()
        known = {p[col]: p for p in people if p[col]}
        for acc in accounts:
            aid = str(acc["id"])
            if aid in known:
                c.execute(f"UPDATE people SET email = CASE WHEN email = '' THEN ? ELSE email END WHERE {col} = ?",
                          (acc.get("email") or "", aid))
                continue
            name, email = acc["name"].strip(), (acc.get("email") or "").strip().lower()
            match = next((p for p in people if not p[col] and (
                p["name"].strip().lower() == name.lower() or (email and p["email"].lower() == email))), None)
            if match:
                c.execute(f"UPDATE people SET {col} = ?, email = CASE WHEN email = '' THEN ? ELSE email END "
                          "WHERE id = ?", (aid, email, match["id"]))
                people = c.execute("SELECT * FROM people").fetchall()
                linked += 1
            else:
                c.execute(f"INSERT INTO people(name, email, {col}) VALUES(?, ?, ?)", (name, email, aid))
                people = c.execute("SELECT * FROM people").fetchall()
                added += 1
        current = {str(a["id"]) for a in accounts}
        for aid in set(known) - current:
            c.execute(f"UPDATE people SET {col} = NULL WHERE {col} = ?", (aid,))
            removed += 1
        orphans = [r["id"] for r in c.execute(
            "SELECT id FROM people WHERE jellyfin_id IS NULL AND plex_id IS NULL").fetchall()]
        for pid in orphans:
            c.execute("DELETE FROM people WHERE id = ?", (pid,))
            c.execute("DELETE FROM suggestions WHERE user_id = ?", (str(pid),))
    return added, linked, removed


def update_person(person_id: int, email: str, enabled: bool, servers: str,
                  jellyfin_id: str | None, plex_id: str | None) -> None:
    with connect() as c:
        # an account can belong to one person only: detach it elsewhere first
        for col, val in (("jellyfin_id", jellyfin_id), ("plex_id", plex_id)):
            if val:
                c.execute(f"UPDATE people SET {col} = NULL WHERE {col} = ? AND id != ?", (val, person_id))
        c.execute(
            "UPDATE people SET email = ?, enabled = ?, servers = ?, jellyfin_id = ?, plex_id = ? WHERE id = ?",
            (email, int(enabled), servers if servers in SERVER_CHOICES else "both",
             jellyfin_id or None, plex_id or None, person_id),
        )


def delete_person(person_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM people WHERE id = ?", (person_id,))
        c.execute("DELETE FROM suggestions WHERE user_id = ?", (str(person_id),))


def mark_sent(person_id: int, ts: str) -> None:
    with connect() as c:
        c.execute("UPDATE people SET last_sent = ? WHERE id = ?", (ts, person_id))


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
