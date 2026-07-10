"""Session helpers + auth decorators.

We use Flask's signed-cookie session for friend logins. Admin endpoints
use a separate Bearer token (config.ADMIN_TOKEN) so the user can curl them
from anywhere without dragging a session cookie around.
"""

from __future__ import annotations

import hmac
from functools import wraps
from typing import Optional

from flask import jsonify, request, session

import config
from auth import db


SESSION_USER_KEY = "uid"


def login(user_id: int) -> None:
    session.clear()
    session[SESSION_USER_KEY] = user_id
    session.permanent = True


def logout() -> None:
    session.clear()


def current_user() -> Optional[dict]:
    uid = session.get(SESSION_USER_KEY)
    if not uid:
        return None
    user = db.get_user(uid)
    if not user:
        # stale session → force clear
        session.clear()
    return user


def require_allowed(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"ok": False, "error": "not signed in"}), 401
        if user["status"] != "allowed":
            return jsonify({"ok": False, "error": "not approved", "status": user["status"]}), 403
        return fn(*args, **kwargs)
    return wrapper


def _token_ok(token: str) -> bool:
    # Compare as bytes — compare_digest on str raises TypeError for
    # non-ASCII input, which would turn a garbage Authorization header
    # into a 500 instead of a 401.
    return hmac.compare_digest(token.encode("utf-8"), config.ADMIN_TOKEN.encode("utf-8"))


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _bearer_token()
        if not token or not _token_ok(token):
            return jsonify({"ok": False, "error": "admin token required"}), 401
        return fn(*args, **kwargs)
    return wrapper


def require_owner(fn):
    """Bearer-token gate for private console routes (/api/hw/*, /api/print/*,
    /api/preview*, /api/image/*, /api/code/*).

    Uses the same ADMIN_TOKEN as /api/admin/*. The main GUI already inlines it
    into the page body; app.js attaches it to every fetch. A second factor
    under the Access gate — the console stays safe even if the edge policy
    is ever misconfigured.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _bearer_token()
        if not token or not _token_ok(token):
            return jsonify({"ok": False, "error": "auth required"}), 401
        return fn(*args, **kwargs)
    return wrapper


def _bearer_token() -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None
