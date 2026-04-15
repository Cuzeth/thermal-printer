"""SQLite for users, credentials, messages.

One file at config.DB_PATH. WAL mode + FKs on. Single-process gunicorn means
we don't need a connection pool; every request opens its own conn.
"""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

import config


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS users (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  username    TEXT    NOT NULL UNIQUE COLLATE NOCASE,
  user_handle BLOB    NOT NULL UNIQUE,
  status      TEXT    NOT NULL DEFAULT 'pending',
  created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
  approved_at TEXT
);

CREATE TABLE IF NOT EXISTS credentials (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  credential_id BLOB    NOT NULL UNIQUE,
  public_key    BLOB    NOT NULL,
  sign_count    INTEGER NOT NULL DEFAULT 0,
  transports    TEXT,
  created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_creds_user ON credentials(user_id);

CREATE TABLE IF NOT EXISTS messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body       TEXT    NOT NULL,
  printed_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_msgs_printed ON messages(printed_at DESC);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    """Create tables on first run. Idempotent."""
    with db() as conn:
        conn.executescript(SCHEMA)


# ---------- users ----------

VALID_STATUSES = ("pending", "allowed", "blocked")


def create_pending_user(username: str) -> dict:
    """Insert a new pending user with a random WebAuthn user_handle.

    Raises sqlite3.IntegrityError if the username is taken.
    """
    handle = secrets.token_bytes(16)
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, user_handle, status) VALUES (?, ?, 'pending')",
            (username, handle),
        )
        return {"id": cur.lastrowid, "username": username, "user_handle": handle, "status": "pending"}


def get_user(user_id: int) -> Optional[dict]:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username: str) -> Optional[dict]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        return dict(row) if row else None


def list_users(status: Optional[str] = None) -> list[dict]:
    sql = "SELECT id, username, status, created_at, approved_at FROM users"
    args: tuple = ()
    if status:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        sql += " WHERE status = ?"
        args = (status,)
    sql += " ORDER BY created_at DESC"
    with db() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def set_status(user_id: int, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    with db() as conn:
        if status == "allowed":
            conn.execute(
                "UPDATE users SET status = ?, approved_at = datetime('now') WHERE id = ?",
                (status, user_id),
            )
        else:
            conn.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))


def delete_user(user_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# ---------- credentials ----------

def add_credential(
    user_id: int,
    credential_id: bytes,
    public_key: bytes,
    sign_count: int,
    transports: Optional[list[str]] = None,
) -> None:
    transports_csv = ",".join(transports) if transports else None
    with db() as conn:
        conn.execute(
            "INSERT INTO credentials (user_id, credential_id, public_key, sign_count, transports) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, credential_id, public_key, sign_count, transports_csv),
        )


def get_credentials_for_user(user_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, credential_id, public_key, sign_count, transports "
            "FROM credentials WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_credential(credential_id: bytes) -> Optional[dict]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM credentials WHERE credential_id = ?", (credential_id,)
        ).fetchone()
        return dict(row) if row else None


def update_sign_count(credential_id: bytes, new_count: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE credentials SET sign_count = ? WHERE credential_id = ?",
            (new_count, credential_id),
        )


# ---------- messages ----------

def log_message(user_id: int, body: str) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (user_id, body) VALUES (?, ?)", (user_id, body)
        )
        return cur.lastrowid


def list_messages(limit: int = 20) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT m.id, m.body, m.printed_at, u.username "
            "FROM messages m JOIN users u ON u.id = m.user_id "
            "ORDER BY m.printed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
