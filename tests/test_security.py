"""Security-surface tests: the admin TOTP gate, rate-limit plumbing,
token comparison edge cases, and response headers.

The admin-gate tests are the important ones — the whole app is on the
public internet now, so the only thing between an anonymous visitor and
the printer console is this login. There is no dev bypass in tests: the
gate runs for real, with codes minted from the well-known conftest
secret."""

from __future__ import annotations

import time

import pytest

import config
import app as app_module
from auth import admin as admin_mod
from auth import blueprint as auth_bp_mod
from auth import ratelimit, totp
from auth import session as sess


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {config.ADMIN_TOKEN}"}


@pytest.fixture(autouse=True)
def clean_admin_state():
    """TOTP failure buckets and the replay guard are process-global;
    reset them so tests can't trip each other's rate limits."""
    admin_mod._failures.clear()
    admin_mod._last_used_step = 0
    yield
    admin_mod._failures.clear()
    admin_mod._last_used_step = 0


def _code_now() -> str:
    return totp.code_at(config.TOTP_SECRET, time.time())


def _wrong_code() -> str:
    """A code guaranteed invalid for the whole accept window."""
    valid = {totp.code_at(config.TOTP_SECRET, time.time() + d)
             for d in (-30, 0, 30)}
    return next(c for c in ("000000", "111111", "222222", "333333")
                if c not in valid)


# ---------- the admin gate ----------

def test_admin_page_serves_login_form_when_signed_out(client):
    r = client.get("/admin")
    assert r.status_code == 200
    assert b"login-form" in r.data
    # The console itself must not leak to anonymous visitors.
    assert b'data-pane="compose"' not in r.data


def test_admin_api_requires_auth(client):
    r = client.post("/api/admin/print/now", json={})
    assert r.status_code == 401
    assert r.get_json()["error"] == "auth required"


def test_totp_login_unlocks_console_and_api(client):
    r = client.post("/api/admin/auth/login", json={"code": _code_now()})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    # Session cookie now carries the admin stamp — no bearer anywhere.
    assert client.get("/api/admin/users").status_code == 200
    assert b'data-pane="compose"' in client.get("/admin").data


def test_totp_login_rejects_wrong_code(client):
    r = client.post("/api/admin/auth/login", json={"code": _wrong_code()})
    assert r.status_code == 401
    assert client.get("/api/admin/users").status_code == 401


def test_totp_login_rejects_replayed_code(client):
    """A code is single-use: the standard TOTP replay guard. The second
    login with the same (still time-valid) code must fail."""
    code = _code_now()
    assert client.post("/api/admin/auth/login", json={"code": code}).status_code == 200
    fresh = app_module.app.test_client()
    r = fresh.post("/api/admin/auth/login", json={"code": code})
    assert r.status_code == 401
    assert "already used" in r.get_json()["error"]


def test_totp_login_rate_limits(client):
    """6-digit codes are a small space — the endpoint has to slam the
    door after repeated failures, and a correct code must not slip
    through during the lockout."""
    wrong = _wrong_code()
    for _ in range(admin_mod._MAX_IP_FAILURES):
        client.post("/api/admin/auth/login", json={"code": wrong})
    r = client.post("/api/admin/auth/login", json={"code": wrong})
    assert r.status_code == 429
    r = client.post("/api/admin/auth/login", json={"code": _code_now()})
    assert r.status_code == 429


def test_totp_login_fails_closed_without_secret(client, monkeypatch):
    """Fresh .env (no TOTP_SECRET) → the login can't succeed, and says
    why instead of pretending the code was wrong."""
    monkeypatch.setattr(config, "TOTP_SECRET", "")
    r = client.post("/api/admin/auth/login", json={"code": "123456"})
    assert r.status_code == 503
    assert "TOTP_SECRET" in r.get_json()["error"]


def test_admin_session_expires(client):
    """The admin stamp is only honored for ADMIN_SESSION_SECONDS; a
    30-day-old friend cookie must not still be an admin cookie."""
    with client.session_transaction() as s:
        s[sess.SESSION_ADMIN_KEY] = time.time() - sess.ADMIN_SESSION_SECONDS - 1
    assert client.get("/api/admin/users").status_code == 401
    assert b"login-form" in client.get("/admin").data


def test_admin_logout_drops_admin_but_keeps_friend_session(client):
    """The owner is often signed in as a friend in the same browser —
    signing out of /admin must not log the friend account out too."""
    from auth import db as auth_db

    user = auth_db.create_pending_user("sec_owner", "hunter2hunter")
    auth_db.set_status(user["id"], "allowed")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    assert client.post("/api/admin/auth/login",
                       json={"code": _code_now()}).status_code == 200
    assert client.post("/api/admin/auth/logout").status_code == 200
    assert client.get("/api/admin/users").status_code == 401
    r = client.get("/api/me")
    assert r.get_json()["user"]["username"] == "sec_owner"


def test_bearer_token_still_works_for_scripts(client, auth):
    """curl + ADMIN_TOKEN is the no-cookie path (smoke tests, scripts) —
    and it must keep working even while the TOTP login is rate-limited."""
    wrong = _wrong_code()
    for _ in range(admin_mod._MAX_IP_FAILURES):
        client.post("/api/admin/auth/login", json={"code": wrong})
    assert client.get("/api/admin/users", headers=auth).status_code == 200


def test_friend_surfaces_are_public(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/me").status_code == 200
    assert client.get("/api/ping").status_code == 200


def test_legacy_m_path_redirects_home(client):
    """Friends' old /m/ bookmarks survive the move to /."""
    for path in ("/m", "/m/"):
        r = client.get(path)
        assert r.status_code == 301
        assert r.headers["Location"].endswith("/")


# ---------- rate-limit plumbing ----------

def test_client_ip_uses_last_xff_hop():
    """The first XFF entry is client-controlled (proxies append); the last
    one is what our local proxy added. Trusting the first would let an
    attacker rotate fake IPs past the per-IP buckets."""
    with app_module.app.test_request_context(
        headers={"X-Forwarded-For": "6.6.6.6, 100.64.0.1"}
    ):
        assert ratelimit.client_ip() == "100.64.0.1"


def test_client_ip_falls_back_to_remote_addr():
    with app_module.app.test_request_context(
        environ_base={"REMOTE_ADDR": "127.0.0.1"}
    ):
        assert ratelimit.client_ip() == "127.0.0.1"


def test_login_success_does_not_clear_ip_bucket():
    """An attacker must not be able to reset the shared per-IP failure
    counter by logging into their own account between guesses."""
    auth_bp_mod._ip_failures["10.0.0.9"] = [9e12]  # far-future timestamp
    auth_bp_mod._failures["victim"] = [9e12]
    try:
        with app_module.app.test_request_context():
            auth_bp_mod._clear_login_failures("victim")
        assert "victim" not in auth_bp_mod._failures
        assert "10.0.0.9" in auth_bp_mod._ip_failures
    finally:
        auth_bp_mod._ip_failures.clear()
        auth_bp_mod._failures.clear()


def test_register_rate_limit_trips(client):
    """6th signup attempt from one IP inside the window → 429."""
    auth_bp_mod._register_attempts.clear()
    try:
        for i in range(auth_bp_mod._MAX_REGISTERS):
            client.post("/api/auth/register",
                        json={"username": f"ratelim_{i}", "password": "longenough"})
        r = client.post("/api/auth/register",
                        json={"username": "ratelim_last", "password": "longenough"})
        assert r.status_code == 429
    finally:
        auth_bp_mod._register_attempts.clear()


# ---------- bearer-token edge cases ----------

def test_non_ascii_bearer_is_401_not_500(client):
    # str-based compare_digest raises TypeError on non-ASCII; must be a
    # clean 401, not a traceback.
    r = client.get("/api/admin/users", headers={"Authorization": "Bearer töken"})
    assert r.status_code == 401


def test_admin_messages_limit_survives_garbage(client, auth):
    r = client.get("/api/admin/messages?limit=banana", headers=auth)
    assert r.status_code == 200


# ---------- response headers ----------

def test_security_headers_present(client):
    r = client.get("/api/ping")
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("Referrer-Policy") == "same-origin"
