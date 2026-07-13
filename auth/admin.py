"""Admin login endpoints — a TOTP code instead of an account.

POST /api/admin/auth/login with the current 6-digit code from the
owner's authenticator app; success stamps the session admin for
ADMIN_SESSION_SECONDS (auth/session.py). No username, no password, no
identity provider — the shared TOTP_SECRET in .env is the whole story.

A 6-digit code is a small space (10^6), so unlike the friend login this
gate carries a *global* failure budget on top of the per-IP one: IPs
are cheap for an attacker, and the only honest client of this endpoint
is one person who types one code a day. Tripping the global bucket
locks the owner out too for 15 minutes (or until a restart) — accepted;
the Bearer ADMIN_TOKEN path doesn't go through this endpoint and keeps
working during a lockout.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

import config
from auth import session as sess
from auth import totp
from auth.ratelimit import client_ip, sliding_check, sliding_record


admin_auth_bp = Blueprint("admin_auth", __name__, url_prefix="/api/admin/auth")

_MAX_IP_FAILURES = 10
_MAX_GLOBAL_FAILURES = 30
_WINDOW_SECONDS = 15 * 60
_GLOBAL_KEY = "*"

_failures: dict[str, list[float]] = {}

# Highest timestep that has already been redeemed. Accepting a step only
# once makes an observed (shoulder-surfed, logged, replayed) code useless
# after its first use — the standard TOTP replay guard. In-process and
# reset on restart, same trust model as the rate-limit buckets.
_last_used_step: int = 0


def _locked() -> bool:
    if sliding_check(_failures, client_ip(), _MAX_IP_FAILURES, _WINDOW_SECONDS):
        return True
    return sliding_check(_failures, _GLOBAL_KEY, _MAX_GLOBAL_FAILURES, _WINDOW_SECONDS)


def _record_failure() -> None:
    sliding_record(_failures, client_ip(), _WINDOW_SECONDS)
    sliding_record(_failures, _GLOBAL_KEY, _WINDOW_SECONDS)


@admin_auth_bp.post("/login")
def login():
    global _last_used_step
    if not config.TOTP_SECRET:
        # Fail closed but say why — a fresh .env shouldn't look like a
        # mistyped code.
        return jsonify({"ok": False, "error": "TOTP_SECRET is not set",
                        "kind": "server"}), 503
    if _locked():
        return jsonify({"ok": False,
                        "error": "too many failed attempts — try again in 15 minutes",
                        "kind": "input"}), 429

    code = str((request.get_json(silent=True) or {}).get("code", ""))
    step = totp.verify(config.TOTP_SECRET, code)
    if step is None:
        _record_failure()
        return jsonify({"ok": False, "error": "wrong code", "kind": "input"}), 401
    if step <= _last_used_step:
        _record_failure()
        return jsonify({"ok": False,
                        "error": "code already used — wait for the next one",
                        "kind": "input"}), 401

    _last_used_step = step
    sess.login_admin()
    return jsonify({"ok": True})


@admin_auth_bp.post("/logout")
def logout():
    sess.logout_admin()
    return jsonify({"ok": True})
