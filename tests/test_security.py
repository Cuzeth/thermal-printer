"""Security-surface tests: the Cloudflare Access gate, rate-limit plumbing,
token comparison edge cases, and response headers.

The Access-gate tests are the important ones — every other test file runs
with DEV_BYPASS_ACCESS=true, which short-circuits require_access entirely.
A route that forgot the decorator would otherwise ship with green CI."""

from __future__ import annotations

import pytest

import config
import app as app_module
from auth import blueprint as auth_bp_mod


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {config.ADMIN_TOKEN}"}


@pytest.fixture
def no_bypass(monkeypatch):
    """Turn the dev Access bypass OFF (and pin an owner identity) so
    require_access actually runs."""
    monkeypatch.setattr(config, "DEV_BYPASS_ACCESS", False)
    monkeypatch.setattr(config, "OWNER_EMAIL", "owner@example.com")


# Headers Cloudflare adds after Access authenticates a visitor. Flask only
# ever sees these on requests the connector already JWT-validated; here we
# inject them directly because the test client plays the role of cloudflared.
ACCESS_HEADERS = {
    "Cf-Access-Jwt-Assertion": "test-jwt-not-validated-here",
    "Cf-Access-Authenticated-User-Email": "owner@example.com",
}


# ---------- Cloudflare Access gate ----------

def test_access_gate_blocks_public_gui(client, no_bypass):
    assert client.get("/").status_code == 403


def test_access_gate_blocks_private_api_even_with_valid_bearer(client, auth, no_bypass):
    # The gate sits OUTSIDE the bearer check — a leaked token alone must
    # not be enough from the public internet.
    r = client.post("/api/print/now", json={}, headers=auth)
    assert r.status_code == 403


def test_access_gate_admits_owner_plus_bearer(client, auth, no_bypass):
    r = client.post("/api/print/now", json={}, headers={**auth, **ACCESS_HEADERS})
    assert r.status_code == 200
    # Email comparison is case-insensitive — Access preserves whatever
    # case the IdP reports, and the owner shouldn't get locked out by it.
    shouty = {**ACCESS_HEADERS,
              "Cf-Access-Authenticated-User-Email": "Owner@Example.COM"}
    assert client.get("/", headers=shouty).status_code == 200


def test_access_gate_pins_owner_email(client, auth, no_bypass):
    # Someone else passing the Access policy (widened by mistake) is still
    # not the owner. Wall three.
    headers = {**auth, **ACCESS_HEADERS,
               "Cf-Access-Authenticated-User-Email": "intruder@example.com"}
    r = client.post("/api/print/now", json={}, headers=headers)
    assert r.status_code == 403


def test_forged_email_without_jwt_marker_is_rejected(client, no_bypass):
    # A forged identity header arriving without the connector's JWT marker
    # (e.g. through a fat-fingered friend-host allowlist) must not pass.
    r = client.get("/", headers={
        "Cf-Access-Authenticated-User-Email": "owner@example.com"})
    assert r.status_code == 403


def test_access_gate_fails_closed_without_owner_email(client, monkeypatch):
    # OWNER_EMAIL unset (fresh .env) → nobody is the owner, not even a
    # fully Access-authenticated visitor. Loud beats open.
    monkeypatch.setattr(config, "DEV_BYPASS_ACCESS", False)
    monkeypatch.setattr(config, "OWNER_EMAIL", "")
    assert client.get("/", headers=ACCESS_HEADERS).status_code == 403


def test_access_identity_alone_is_not_enough_for_private_api(client, no_bypass):
    # Authenticated owner but no bearer → 401 from require_owner.
    r = client.post("/api/print/now", json={}, headers=ACCESS_HEADERS)
    assert r.status_code == 401


def test_friend_routes_stay_public_without_access(client, no_bypass):
    assert client.get("/m/").status_code == 200
    assert client.get("/api/m/me").status_code == 200
    assert client.get("/api/ping").status_code == 200


# ---------- rate-limit plumbing ----------

def test_client_ip_uses_last_xff_hop():
    """The first XFF entry is client-controlled (proxies append); the last
    one is what our local proxy added. Trusting the first would let an
    attacker rotate fake IPs past the per-IP buckets."""
    with app_module.app.test_request_context(
        headers={"X-Forwarded-For": "6.6.6.6, 100.64.0.1"}
    ):
        assert auth_bp_mod._client_ip() == "100.64.0.1"


def test_client_ip_falls_back_to_remote_addr():
    with app_module.app.test_request_context(
        environ_base={"REMOTE_ADDR": "127.0.0.1"}
    ):
        assert auth_bp_mod._client_ip() == "127.0.0.1"


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
            client.post("/api/m/auth/register",
                        json={"username": f"ratelim_{i}", "password": "longenough"})
        r = client.post("/api/m/auth/register",
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
