"""SQLite for users + messages.

One file at config.DB_PATH. WAL mode + FKs on. Single-process gunicorn means
we don't need a connection pool; every request opens its own conn.

Schema note: this file assumes a fresh DB. If you have a pre-existing
`app.db` from the old passkey-based schema, delete it before the next
boot (`rm data/app.db`) — init() won't migrate the old columns.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

from werkzeug.security import check_password_hash, generate_password_hash

import config


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT    NOT NULL,
  status        TEXT    NOT NULL DEFAULT 'pending',
  name_style    TEXT    NOT NULL DEFAULT 'plain',
  created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
  approved_at   TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body       TEXT    NOT NULL,
  status     TEXT    NOT NULL DEFAULT 'printed',
  printed_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_msgs_printed ON messages(printed_at DESC);
"""

# Styles a friend can pick for how their name prints on the header line.
# Must stay in sync with render.NAME_STYLES + 'plain' (the no-style default
# that renders as a normal `## from <name>` subheading).
VALID_NAME_STYLES = ("plain", "big", "caps", "serif", "script", "gothic", "mono")


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
    """Create tables on first run. Idempotent.

    Also runs tiny forward migrations for columns added after launch, so a
    Pi with an existing app.db doesn't need a manual schema dump. Each
    migration is a single ALTER wrapped in a pragma-check — SQLite lacks
    `ADD COLUMN IF NOT EXISTS`.
    """
    with db() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "name_style" not in cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN name_style TEXT NOT NULL DEFAULT 'plain'"
            )
        msg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        if "status" not in msg_cols:
            # Pre-status rows were logged synchronously at print time, so
            # 'printed' is the honest backfill.
            conn.execute(
                "ALTER TABLE messages ADD COLUMN status TEXT NOT NULL DEFAULT 'printed'"
            )


# ---------- users ----------

VALID_STATUSES = ("pending", "allowed", "blocked")


def create_pending_user(username: str, password: str) -> dict:
    """Insert a new pending user with a hashed password.

    Raises sqlite3.IntegrityError if the username is taken.
    """
    hashed = generate_password_hash(password)
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, status) VALUES (?, ?, 'pending')",
            (username, hashed),
        )
        return {
            "id": cur.lastrowid,
            "username": username,
            "status": "pending",
        }


def verify_password(user: dict, password: str) -> bool:
    """Constant-time compare against the stored hash."""
    return check_password_hash(user.get("password_hash") or "", password)


def set_password(user_id: int, password: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), user_id),
        )


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


def set_name_style(user_id: int, style: str) -> None:
    if style not in VALID_NAME_STYLES:
        raise ValueError(f"invalid name_style: {style}")
    with db() as conn:
        conn.execute(
            "UPDATE users SET name_style = ? WHERE id = ?", (style, user_id)
        )


# ---------- messages ----------

# Lifecycle of a friend print: logged 'queued' at enqueue, flipped to
# 'printed' or 'failed' by the queue worker. Rows written synchronously
# (tests, pre-status data) default to 'printed'.
VALID_MESSAGE_STATUSES = ("queued", "printed", "failed")


def log_message(user_id: int, body: str, status: str = "printed") -> int:
    if status not in VALID_MESSAGE_STATUSES:
        raise ValueError(f"invalid message status: {status}")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (user_id, body, status) VALUES (?, ?, ?)",
            (user_id, body, status),
        )
        return cur.lastrowid


def set_message_status(message_id: int, status: str) -> None:
    if status not in VALID_MESSAGE_STATUSES:
        raise ValueError(f"invalid message status: {status}")
    with db() as conn:
        conn.execute(
            "UPDATE messages SET status = ? WHERE id = ?", (status, message_id)
        )


def delete_message(message_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))


def list_messages(limit: int = 20) -> list[dict]:
    with db() as conn:
        # Tie-break on id so same-second prints sort newest-first too.
        rows = conn.execute(
            "SELECT m.id, m.body, m.status, m.printed_at, u.username "
            "FROM messages m JOIN users u ON u.id = m.user_id "
            "ORDER BY m.printed_at DESC, m.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_messages_for_user(user_id: int, limit: int = 50) -> list[dict]:
    """Every print this user has made, newest first. Powers the personal
    history panel on /m/ — no JOIN to users (it's always the caller's own
    row), no username field in the result.

    `printed_at` is second-precision in SQLite, so two rapid prints can tie.
    We break the tie with `id DESC` to keep the newest-first ordering stable
    — otherwise a bursty double-print would show in the wrong order."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, body, status, printed_at FROM messages "
            "WHERE user_id = ? ORDER BY printed_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
