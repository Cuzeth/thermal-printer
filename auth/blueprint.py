"""Friend-facing auth endpoints. Mounted at /api/m/auth/* by app.py.

Wire-format note: bytes (challenge, credential_id, public_key) cross the
session boundary base64url-encoded. The `webauthn` helpers handle it on
the way out (options_to_json) and we handle it manually for the inbound
payload from `navigator.credentials.{create,get}()`.
"""

from __future__ import annotations

import json
import re
import sqlite3

from flask import Blueprint, jsonify, request, session
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from auth import db, session as sess, webauthn_flow as wa


auth_bp = Blueprint("auth", __name__, url_prefix="/api/m")

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,20}$")

# session keys for the challenge round-trip
CH_REG = "ch_register"
CH_LOGIN = "ch_login"
PENDING_USER = "pending_register_user_id"  # set during register/begin


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


# ---------- registration ----------

@auth_bp.post("/auth/register/begin")
def register_begin():
    data = request.get_json(silent=True) or {}
    try:
        username = _validate_username(data.get("username", ""))
    except ValueError as e:
        return _bad(str(e))

    # Reject if username taken — peek so we can give a clean 409 instead of
    # leaking the IntegrityError.
    if db.get_user_by_username(username):
        return _bad("username taken", 409)

    try:
        user = db.create_pending_user(username)
    except sqlite3.IntegrityError:
        return _bad("username taken", 409)

    options_json, challenge = wa.begin_registration(
        username=user["username"],
        user_handle=user["user_handle"],
        exclude_ids=[],
    )
    session.clear()  # don't carry stale state into a new registration
    session[CH_REG] = bytes_to_base64url(challenge)
    session[PENDING_USER] = user["id"]
    return jsonify({"ok": True, "options": json.loads(options_json)})


@auth_bp.post("/auth/register/finish")
def register_finish():
    challenge_b64 = session.get(CH_REG)
    user_id = session.get(PENDING_USER)
    if not challenge_b64 or not user_id:
        return _bad("no registration in progress", 400)

    payload = request.get_json(silent=True) or {}
    if not payload:
        return _bad("missing credential")

    try:
        verified = wa.finish_registration(
            credential=payload,
            expected_challenge=base64url_to_bytes(challenge_b64),
        )
    except Exception as e:
        # On failure, drop the pending user so the username is freed up.
        db.delete_user(user_id)
        session.clear()
        return _bad(f"registration failed: {e}", 400)

    db.add_credential(
        user_id=user_id,
        credential_id=verified.credential_id,
        public_key=verified.public_key,
        sign_count=verified.sign_count,
        transports=verified.transports,
    )
    # Sign them in to a "pending" session so the UI can poll status.
    sess.login(user_id)
    user = db.get_user(user_id)
    return jsonify({"ok": True, "user": {"id": user["id"], "username": user["username"], "status": user["status"]}})


# ---------- authentication ----------

@auth_bp.post("/auth/login/begin")
def login_begin():
    data = request.get_json(silent=True) or {}
    try:
        username = _validate_username(data.get("username", ""))
    except ValueError as e:
        return _bad(str(e))

    user = db.get_user_by_username(username)
    if not user:
        return _bad("no such user", 404)
    creds = db.get_credentials_for_user(user["id"])
    if not creds:
        return _bad("no passkey on file", 404)

    options_json, challenge = wa.begin_authentication(
        allow_ids=[c["credential_id"] for c in creds]
    )
    session.clear()
    session[CH_LOGIN] = bytes_to_base64url(challenge)
    return jsonify({"ok": True, "options": json.loads(options_json)})


@auth_bp.post("/auth/login/finish")
def login_finish():
    challenge_b64 = session.get(CH_LOGIN)
    if not challenge_b64:
        return _bad("no login in progress", 400)

    payload = request.get_json(silent=True) or {}
    raw_id_b64 = payload.get("id") or payload.get("rawId")
    if not raw_id_b64:
        return _bad("missing credential id")

    raw_id = base64url_to_bytes(raw_id_b64)
    cred = db.get_credential(raw_id)
    if not cred:
        return _bad("unknown credential", 404)

    try:
        verified = wa.finish_authentication(
            credential=payload,
            expected_challenge=base64url_to_bytes(challenge_b64),
            public_key=cred["public_key"],
            current_sign_count=cred["sign_count"],
        )
    except Exception as e:
        return _bad(f"login failed: {e}", 400)

    db.update_sign_count(raw_id, verified.new_sign_count)
    sess.login(cred["user_id"])
    user = db.get_user(cred["user_id"])
    return jsonify({"ok": True, "user": {"id": user["id"], "username": user["username"], "status": user["status"]}})


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
    return jsonify({"ok": True, "user": {"id": user["id"], "username": user["username"], "status": user["status"]}})
