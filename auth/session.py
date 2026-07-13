"""Session helpers + auth decorators.

We use Flask's signed-cookie session for friend logins AND the admin
login — the admin flag is just an extra timestamp key in the same
cookie, so the owner can be signed in as a friend and as admin at once.

Admin routes also accept a Bearer token (config.ADMIN_TOKEN) so the
owner can curl them from anywhere without dragging a session cookie
around; scripts and the smoke tests use that path.
"""

from __future__ import annotations

import hmac
import time
from functools import wraps
from typing import Optional

from flask import jsonify, request, session

import config
from auth import db


SESSION_USER_KEY = "uid"

# Epoch stamp set by a successful TOTP login. The cookie itself lives for
# 30 days (friend sessions want that); this stamp is what bounds *admin*
# validity, so the owner re-types a code roughly once a day instead of
# holding a monthlong admin cookie.
SESSION_ADMIN_KEY = "admin_at"
ADMIN_SESSION_SECONDS = 12 * 60 * 60


def login(user_id: int) -> None:
    # Preserve an admin stamp across a friend login — the owner signing
    # into their own friend account shouldn't get logged out of /admin.
    admin_at = session.get(SESSION_ADMIN_KEY)
    session.clear()
    if admin_at is not None:
        session[SESSION_ADMIN_KEY] = admin_at
    session[SESSION_USER_KEY] = user_id
    session.permanent = True


def logout() -> None:
    admin_at = session.get(SESSION_ADMIN_KEY)
    session.clear()
    if admin_at is not None:
        session[SESSION_ADMIN_KEY] = admin_at


def login_admin() -> None:
    session[SESSION_ADMIN_KEY] = time.time()
    session.permanent = True


def logout_admin() -> None:
    session.pop(SESSION_ADMIN_KEY, None)


def is_admin_session() -> bool:
    stamp = session.get(SESSION_ADMIN_KEY)
    if not isinstance(stamp, (int, float)):
        return False
    return (time.time() - stamp) < ADMIN_SESSION_SECONDS


def current_user() -> Optional[dict]:
    uid = session.get(SESSION_USER_KEY)
    if not uid:
        return None
    user = db.get_user(uid)
    if not user:
        # stale session → force clear (keeps any admin stamp)
        session.pop(SESSION_USER_KEY, None)
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


def is_admin_request() -> bool:
    """Admin = a live TOTP session, a valid Bearer ADMIN_TOKEN, or the
    local-dev bypass. One gate for the console page and every /api/admin
    route — there is no separate owner identity anymore."""
    if config.DEV_BYPASS_ADMIN:
        return True
    if is_admin_session():
        return True
    token = _bearer_token()
    return bool(token) and _token_ok(token)


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_admin_request():
            return jsonify({"ok": False, "error": "auth required"}), 401
        return fn(*args, **kwargs)
    return wrapper


def _bearer_token() -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None
