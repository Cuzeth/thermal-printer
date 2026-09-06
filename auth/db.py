"""SQLite for users + print history.

One file at config.DB_PATH. WAL mode + FKs on. Single-process gunicorn means
we don't need a connection pool; every request opens its own conn.

Schema note: this file assumes a fresh DB. If you have a pre-existing
`app.db` from the old passkey-based schema, delete it before the next
boot (`rm data/app.db`) — init() won't migrate the old columns.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

from werkzeug.security import check_password_hash, generate_password_hash

import config


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS users (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  username            TEXT    NOT NULL UNIQUE COLLATE NOCASE,
  password_hash       TEXT    NOT NULL,
  status              TEXT    NOT NULL DEFAULT 'pending',
  name_style          TEXT    NOT NULL DEFAULT 'plain',
  created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
  approved_at         TEXT,
  reset_token_hash    TEXT,
  reset_token_expires TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body       TEXT    NOT NULL,
  drawing    BLOB,
  kind       TEXT    NOT NULL DEFAULT 'text',
  anonymous  INTEGER NOT NULL DEFAULT 0,
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
        if "reset_token_hash" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN reset_token_hash TEXT")
        if "reset_token_expires" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN reset_token_expires TEXT")
        msg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        if "status" not in msg_cols:
            # Pre-status rows were logged synchronously at print time, so
            # 'printed' is the honest backfill.
            conn.execute(
                "ALTER TABLE messages ADD COLUMN status TEXT NOT NULL DEFAULT 'printed'"
            )
        if "drawing" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN drawing BLOB")
        if "anonymous" not in msg_cols:
            # Needed so an owner retry reprints the receipt the way the
            # friend chose it. Pre-column rows default to named, which is
            # the common case; the anonymous ones can't be recovered.
            conn.execute(
                "ALTER TABLE messages ADD COLUMN anonymous INTEGER NOT NULL DEFAULT 0"
            )
        if "kind" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'text'")
            # Existing raster rows are doodles; photos have an explicit kind
            # so their tall strips never get restored onto the square canvas.
            conn.execute("UPDATE messages SET kind = 'doodle' WHERE drawing IS NOT NULL")
        for column in ("deliver_at", "requested_for"):
            if column not in msg_cols:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_msgs_delivery ON messages(status, deliver_at)")


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


# ---------- password reset links ----------

# How long an admin-minted reset link stays redeemable. Long enough for
# "texted the link, friend opens it after dinner", short enough that a
# link rotting in a chat history isn't a standing credential.
RESET_TOKEN_MINUTES = 60


def _reset_digest(token: str) -> str:
    # Plain sha256, not werkzeug's salted hash: we need to *look up* the
    # row by token, and the token is already 256 bits of secrets-module
    # randomness, so salting adds nothing.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_reset_token(user_id: int) -> str:
    """Mint a single-use forgot-password token for this user.

    Returns the raw token — the only copy that ever exists; the DB keeps
    just its sha256, so a leaked database can't be turned into working
    reset links. Minting again overwrites the previous token, so at most
    one link per user is live at a time."""
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute(
            "UPDATE users SET reset_token_hash = ?, "
            f"reset_token_expires = datetime('now', '+{RESET_TOKEN_MINUTES} minutes') "
            "WHERE id = ?",
            (_reset_digest(token), user_id),
        )
    return token


def consume_reset_token(token: str) -> Optional[dict]:
    """Redeem a reset token: return its user and burn the token, or None
    if it matches nothing or has expired. Burning happens even before the
    caller sets the new password — a reset link is strictly one shot."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE reset_token_hash = ? "
            "AND reset_token_expires > datetime('now')",
            (_reset_digest(token),),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE users SET reset_token_hash = NULL, reset_token_expires = NULL "
            "WHERE id = ?",
            (row["id"],),
        )
        return dict(row)


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
            # Revocation also burns pending capsules, so approving again later
            # cannot unexpectedly deliver an old backlog.
            conn.execute(
                "UPDATE messages SET status = 'cancelled' WHERE user_id = ? "
                "AND deliver_at IS NOT NULL AND status IN ('scheduled', 'queued')",
                (user_id,),
            )


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

# Lifecycle of a friend print: 'scheduled' while waiting for a delivery
# date or quiet hours, 'queued' after dispatch, then 'printed' or 'failed'.
# Friends can cancel while scheduled; revocation cancels pending capsules.
# Rows written synchronously
# (tests, pre-status data) default to 'printed'.
#
# The row is the durable job. It holds the raw body or the saved drawing
# plus the anonymous flag, and the worker rebuilds the print from it —
# so a row still 'queued' after a restart is simply replayed
# (app._replay_queued), not lost.
VALID_MESSAGE_STATUSES = ("scheduled", "queued", "printed", "failed", "cancelled")


class CapsuleLimit(ValueError):
    """A durable pending limit was reached, distinct from invalid content."""


def schedule_message(user_id: int, body: str, deliver_at: str,
                     requested_for: str | None, drawing: bytes | None = None,
                     anonymous: bool = False, kind: str | None = None) -> int:
    from features.delivery import PER_USER_CAP, TOTAL_CAP

    kind = kind or ("doodle" if drawing is not None else "text")
    if kind not in ("text", "doodle", "photo"):
        raise ValueError(f"invalid message kind: {kind}")
    with db() as conn:
        # Reserve the SQLite writer before counting: simultaneous submissions
        # must share the limits, including capsules already claimed by the queue.
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM users WHERE id = ? AND status = 'allowed'",
                            (user_id,)).fetchone():
            raise ValueError("sender is no longer approved")
        counts = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(user_id = ?), 0) AS own "
            "FROM messages WHERE deliver_at IS NOT NULL "
            "AND status IN ('scheduled', 'queued')", (user_id,),
        ).fetchone()
        if counts["own"] >= PER_USER_CAP:
            raise CapsuleLimit(f"{PER_USER_CAP} capsules already waiting; cancel one first")
        if counts["total"] >= TOTAL_CAP:
            raise CapsuleLimit("capsule storage full; try again after deliveries finish")
        cur = conn.execute(
            "INSERT INTO messages (user_id, body, status, drawing, anonymous, kind, "
            "deliver_at, requested_for) VALUES (?, ?, 'scheduled', ?, ?, ?, ?, ?)",
            (user_id, body, drawing, int(anonymous), kind, deliver_at, requested_for),
        )
        return cur.lastrowid


def due_message_ids(now: str) -> list[tuple[int, int]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT user_id, id FROM messages WHERE status = 'scheduled' "
            "AND deliver_at <= ? ORDER BY deliver_at, id", (now,),
        ).fetchall()
        return [(r["user_id"], r["id"]) for r in rows]


def claim_scheduled(message_id: int, now: str) -> bool:
    """Cancellation and claim compete on this conditional write; one wins."""
    with db() as conn:
        cur = conn.execute(
            "UPDATE messages SET status = 'queued' WHERE id = ? "
            "AND status = 'scheduled' AND deliver_at <= ? "
            "AND user_id IN (SELECT id FROM users WHERE status = 'allowed')",
            (message_id, now),
        )
        return cur.rowcount == 1


def defer_message(message_id: int, deliver_at: str) -> bool:
    """Release a queue claim for quiet hours or queue pressure, never revive cancellation."""
    with db() as conn:
        cur = conn.execute(
            "UPDATE messages SET status = 'scheduled', deliver_at = ? "
            "WHERE id = ? AND status IN ('queued', 'scheduled')", (deliver_at, message_id),
        )
        return cur.rowcount == 1


def cancel_scheduled(message_id: int, user_id: int) -> bool:
    with db() as conn:
        cur = conn.execute(
            "UPDATE messages SET status = 'cancelled' WHERE id = ? "
            "AND user_id = ? AND status = 'scheduled'", (message_id, user_id),
        )
        return cur.rowcount == 1


def log_message(
    user_id: int,
    body: str,
    status: str = "printed",
    drawing: bytes | None = None,
    anonymous: bool = False,
    kind: str | None = None,
) -> int:
    if status not in VALID_MESSAGE_STATUSES:
        raise ValueError(f"invalid message status: {status}")
    kind = kind or ("doodle" if drawing is not None else "text")
    if kind not in ("text", "doodle", "photo"):
        raise ValueError(f"invalid message kind: {kind}")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (user_id, body, status, drawing, anonymous, kind) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, body, status, drawing, int(anonymous), kind),
        )
        return cur.lastrowid


def get_message(message_id: int) -> dict | None:
    """One history row with everything needed to rebuild the print: the
    raw body or saved drawing, when it was sent, the friend's current
    name style, and whether they sent it anonymously. Used by the queue
    worker and the owner's retry. No user scoping, unlike
    get_message_drawing_for_user."""
    with db() as conn:
        row = conn.execute(
            "SELECT m.id, m.user_id, m.body, m.status, m.drawing, m.anonymous, m.kind, "
            "m.printed_at, m.deliver_at, m.requested_for, u.username, u.name_style, "
            "u.status AS user_status "
            "FROM messages m JOIN users u ON u.id = m.user_id WHERE m.id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            return None
        msg = dict(row)
        msg["drawing"] = bytes(row["drawing"]) if row["drawing"] is not None else None
        msg["anonymous"] = bool(row["anonymous"])
        return msg


def list_queued_message_ids() -> list[tuple[int, int]]:
    """(user_id, message_id) for every row still 'queued', oldest first —
    what the worker should pick back up after a restart."""
    with db() as conn:
        rows = conn.execute(
            "SELECT user_id, id FROM messages WHERE status = 'queued' ORDER BY id"
        ).fetchall()
        return [(r["user_id"], r["id"]) for r in rows]


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
            "SELECT m.id, m.body, m.status, m.printed_at, m.deliver_at, u.username "
            "FROM messages m JOIN users u ON u.id = m.user_id "
            "ORDER BY m.printed_at DESC, m.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_messages_for_user(user_id: int, limit: int = 50) -> list[dict]:
    """Pending capsules first, then recent prints newest first. Powers the personal
    history panel on the friends page — no JOIN to users (it's always the caller's own
    row), no username field in the result.

    `printed_at` is second-precision in SQLite, so two rapid prints can tie.
    We break the tie with `id DESC` to keep the newest-first ordering stable.
    Scheduled rows stay at the top so 50 newer prints cannot hide a capsule's
    cancel action before its future delivery date."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, body, kind, anonymous, status, printed_at, deliver_at, requested_for, "
            "drawing IS NOT NULL AS has_drawing FROM messages "
            "WHERE user_id = ? ORDER BY (status = 'scheduled') DESC, printed_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) | {"has_drawing": bool(r["has_drawing"]),
                           "anonymous": bool(r["anonymous"])} for r in rows]


def get_message_drawing_for_user(message_id: int, user_id: int) -> bytes | None:
    """Return one saved drawing, scoped to its owner.

    Missing rows, text rows, and another friend's rows intentionally all
    look alike so this helper cannot be used to probe history ids.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT drawing FROM messages WHERE id = ? AND user_id = ? "
            "AND drawing IS NOT NULL",
            (message_id, user_id),
        ).fetchone()
        return bytes(row["drawing"]) if row else None
