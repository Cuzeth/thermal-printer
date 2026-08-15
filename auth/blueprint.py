"""Friend-facing auth endpoints. Mounted at /api/auth/* by app.py.

Simple username + password. Passwords hashed with werkzeug's scrypt
helper (ships with Flask, no new dep). Failed-login rate limit is an
in-memory per-username sliding window — resets on restart, fine for a
small-audience app behind Cloudflare.
"""

from __future__ import annotations

import re
import sqlite3

from flask import Blueprint, jsonify, request

from auth import db, session as sess
from auth.ratelimit import client_ip as _client_ip
from auth.ratelimit import sliding_check as _sliding_check
from auth.ratelimit import sliding_record as _sliding_record


auth_bp = Blueprint("auth", __name__, url_prefix="/api")

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,20}$")
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 200

# Sliding-window rate limits. Per-username is tight so a targeted account
# gets protection; per-IP is looser (household NAT has many users) but bounds
# credential stuffing across usernames. Per-IP on register blunts signup
# spam. Buckets reset on restart — fine for the trust model.
_MAX_FAILURES = 10
_WINDOW_SECONDS = 15 * 60
_MAX_IP_FAILURES = 30
_MAX_REGISTERS = 5
_REGISTER_WINDOW = 60 * 60
_failures: dict[str, list[float]] = {}
_ip_failures: dict[str, list[float]] = {}
_register_attempts: dict[str, list[float]] = {}


# ---------- helpers ----------

def _bad(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


def _validate_username(username: str) -> str:
    if not username or not isinstance(username, str):
        raise ValueError("enter a username")
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise ValueError("use 1-20 letters, numbers, _ or -")
    return username


def validate_password(password: str) -> str:
    """Shared by register and the reset-link redeem — same rules apply."""
    if not password or not isinstance(password, str):
        raise ValueError("enter a password")
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"use at least {MIN_PASSWORD_LEN} characters")
    if len(password) > MAX_PASSWORD_LEN:
        raise ValueError(f"password limit: {MAX_PASSWORD_LEN} characters")
    return password


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "status": user["status"],
        # Default shields old rows where the migration hasn't populated yet.
        "name_style": user.get("name_style") or "plain",
    }


# ---------- login rate limit ----------

def _login_locked(username: str) -> bool:
    """Locked out by either bucket?"""
    if _sliding_check(_failures, username.lower(), _MAX_FAILURES, _WINDOW_SECONDS):
        return True
    if _sliding_check(_ip_failures, _client_ip(), _MAX_IP_FAILURES, _WINDOW_SECONDS):
        return True
    return False


def _record_login_failure(username: str) -> None:
    _sliding_record(_failures, username.lower(), _WINDOW_SECONDS)
    _sliding_record(_ip_failures, _client_ip(), _WINDOW_SECONDS)


def _clear_login_failures(username: str) -> None:
    # Only the per-username bucket. Clearing the IP bucket on success would
    # let an attacker reset the shared counter by logging into their own
    # account between guesses at someone else's password.
    _failures.pop(username.lower(), None)


def _register_locked() -> bool:
    return _sliding_check(_register_attempts, _client_ip(),
                          _MAX_REGISTERS, _REGISTER_WINDOW)


def _record_register(ip: str) -> None:
    _sliding_record(_register_attempts, ip, _REGISTER_WINDOW)


# ---------- register ----------

@auth_bp.post("/auth/register")
def register():
    if _register_locked():
        return _bad("too many signups. try again later", 429)

    data = request.get_json(silent=True) or {}
    try:
        username = _validate_username(data.get("username", ""))
        password = validate_password(data.get("password", ""))
    except ValueError as e:
        _record_register(_client_ip())
        return _bad(str(e))

    if db.get_user_by_username(username):
        _record_register(_client_ip())
        return _bad("username unavailable", 409)

    try:
        user = db.create_pending_user(username, password)
    except sqlite3.IntegrityError:
        _record_register(_client_ip())
        return _bad("username unavailable", 409)

    _record_register(_client_ip())
    sess.login(user["id"])
    full = db.get_user(user["id"])
    return jsonify({"ok": True, "user": _public_user(full)})


# ---------- login ----------

@auth_bp.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return _bad("enter a username and password")

    if _login_locked(username):
        return _bad("too many attempts. try again in 15 minutes", 429)

    user = db.get_user_by_username(username)
    if not user or not db.verify_password(user, password):
        _record_login_failure(username)
        return _bad("username or password is wrong", 401)

    _clear_login_failures(username)
    sess.login(user["id"])
    return jsonify({"ok": True, "user": _public_user(user)})


# ---------- password reset ----------

@auth_bp.post("/auth/reset")
def reset_password():
    """Redeem an admin-minted forgot-password link: the friend picks a new
    password and lands signed in, so a reset never needs a second trip
    through the login form.

    The token is 256 bits of randomness, so guessing is hopeless — but
    failed redeems still count against the shared per-IP login bucket to
    bound the noise from anyone poking at the endpoint."""
    if _sliding_check(_ip_failures, _client_ip(), _MAX_IP_FAILURES, _WINDOW_SECONDS):
        return _bad("too many attempts. try again in 15 minutes", 429)

    data = request.get_json(silent=True) or {}
    token = data.get("token") or ""
    if not token or not isinstance(token, str):
        return _bad("reset link missing")
    # Validate the password *before* consuming the token, so a typo'd
    # too-short password doesn't burn the friend's one-shot link.
    try:
        password = validate_password(data.get("password", ""))
    except ValueError as e:
        return _bad(str(e))

    user = db.consume_reset_token(token)
    if not user:
        _sliding_record(_ip_failures, _client_ip(), _WINDOW_SECONDS)
        return _bad("link expired. text me")

    db.set_password(user["id"], password)
    # They almost certainly racked up failed logins figuring out they'd
    # forgotten the password — don't make the new one sit out a lockout.
    _clear_login_failures(user["username"])
    sess.login(user["id"])
    return jsonify({"ok": True, "user": _public_user(user)})


# ---------- session ----------

@auth_bp.post("/auth/logout")
def logout():
    sess.logout()
    return jsonify({"ok": True})


@auth_bp.get("/me")
def me():
    user = sess.current_user()
    if not user:
        return jsonify({"ok": True, "user": None})
    return jsonify({"ok": True, "user": _public_user(user)})


@auth_bp.post("/settings")
def settings():
    """Update the signed-in friend's personal display settings.

    Currently only handles `name_style`. Rejects unknown styles server-side
    instead of trusting the client — the set is small and curated.
    """
    user = sess.current_user()
    if not user:
        return _bad("not signed in", 401)
    if user["status"] != "allowed":
        return _bad("account not approved", 403)
    data = request.get_json(silent=True) or {}
    style = (data.get("name_style") or "").strip()
    if style:
        if style not in db.VALID_NAME_STYLES:
            return _bad(f"unknown name style: {style}")
        db.set_name_style(user["id"], style)
    fresh = db.get_user(user["id"])
    return jsonify({"ok": True, "user": _public_user(fresh)})
