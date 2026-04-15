"""Friend-facing auth endpoints. Mounted at /api/m/auth/* by app.py.

Simple username + password. Passwords hashed with werkzeug's scrypt
helper (ships with Flask, no new dep). Failed-login rate limit is an
in-memory per-username sliding window — resets on restart, fine for a
small-audience app behind Funnel.
"""

from __future__ import annotations

import re
import sqlite3
import time

from flask import Blueprint, jsonify, request

from auth import db, session as sess


auth_bp = Blueprint("auth", __name__, url_prefix="/api/m")

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,20}$")
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 200

# Sliding-window rate limit for failed logins.
_MAX_FAILURES = 10
_WINDOW_SECONDS = 15 * 60
_failures: dict[str, list[float]] = {}


# ---------- helpers ----------

def _bad(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


def _validate_username(username: str) -> str:
    if not username or not isinstance(username, str):
        raise ValueError("username required")
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise ValueError("username must be 1-20 chars, letters/digits/_/- only")
    return username


def _validate_password(password: str) -> str:
    if not password or not isinstance(password, str):
        raise ValueError("password required")
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    if len(password) > MAX_PASSWORD_LEN:
        raise ValueError(f"password too long (max {MAX_PASSWORD_LEN})")
    return password


def _public_user(user: dict) -> dict:
    return {"id": user["id"], "username": user["username"], "status": user["status"]}


# ---------- login rate limit ----------

def _prune(username_key: str) -> None:
    cutoff = time.time() - _WINDOW_SECONDS
    arr = [t for t in _failures.get(username_key, []) if t > cutoff]
    if arr:
        _failures[username_key] = arr
    else:
        _failures.pop(username_key, None)


def _too_many_failures(username: str) -> bool:
    key = username.lower()
    _prune(key)
    return len(_failures.get(key, [])) >= _MAX_FAILURES


def _record_failure(username: str) -> None:
    key = username.lower()
    _prune(key)
    _failures.setdefault(key, []).append(time.time())


def _clear_failures(username: str) -> None:
    _failures.pop(username.lower(), None)


# ---------- register ----------

@auth_bp.post("/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    try:
        username = _validate_username(data.get("username", ""))
        password = _validate_password(data.get("password", ""))
    except ValueError as e:
        return _bad(str(e))

    if db.get_user_by_username(username):
        return _bad("username taken", 409)

    try:
        user = db.create_pending_user(username, password)
    except sqlite3.IntegrityError:
        return _bad("username taken", 409)

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
        return _bad("username and password required")

    if _too_many_failures(username):
        return _bad("too many failed attempts — try again in 15 minutes", 429)

    user = db.get_user_by_username(username)
    if not user or not db.verify_password(user, password):
        _record_failure(username)
        return _bad("wrong username or password", 401)

    _clear_failures(username)
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
