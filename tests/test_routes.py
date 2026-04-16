"""HTTP-surface tests — owner auth gate and basic routing. Everything runs
in DRY_RUN so no USB is touched."""

from __future__ import annotations

import pytest

import config
import app as app_module


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {config.ADMIN_TOKEN}"}


def test_ping_is_public(client):
    r = client.get("/api/ping")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_private_route_requires_bearer(client):
    r = client.post("/api/print/quote", json={})
    assert r.status_code == 401
    assert r.get_json()["error"] == "auth required"


def test_private_route_rejects_wrong_bearer(client):
    r = client.post("/api/print/quote", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_private_route_accepts_owner_bearer(client, auth):
    r = client.post("/api/print/quote", json={}, headers=auth)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_admin_and_owner_share_the_same_token(client, auth):
    # Admin routes still work with the same bearer — today they share it.
    r = client.get("/api/admin/users", headers=auth)
    assert r.status_code == 200


def test_preview_rich_returns_segments(client, auth):
    r = client.post("/api/preview/rich", json={"body": "# HI\n!!!\ngoodbye"}, headers=auth)
    assert r.status_code == 200
    segs = r.get_json()["segments"]
    assert len(segs) == 2
    for s in segs:
        assert s.startswith("data:image/png;base64,")


def test_hw_raw_rejects_oversized_payload(client, auth):
    # Build a hex blob that parses to > 4096 bytes.
    payload = " ".join(["20"] * 5000)
    r = client.post("/api/hw/raw", json={"bytes": payload}, headers=auth)
    assert r.status_code == 400
    assert "max is 4096" in r.get_json()["error"]


def test_hw_raw_accepts_small_payload(client, auth):
    r = client.post("/api/hw/raw", json={"bytes": "1b 40"}, headers=auth)
    assert r.status_code == 200
    assert r.get_json()["sent"] == 2


def test_friend_print_requires_session(client):
    # No session cookie → 401.
    r = client.post("/api/m/print", json={"body": "hi"})
    assert r.status_code == 401


def test_friend_print_returns_503_when_printer_offline(client, monkeypatch):
    """End-to-end: if the USB printer is unreachable mid-print, the friend
    gets a clean 503 with our friendly message — not a 500 traceback."""
    import config as cfg
    import printer
    from auth import db as auth_db, session as sess

    class _Offline:
        def __init__(self, *a, **k): pass
        def open(self):
            raise type("DeviceNotFoundError", (Exception,), {})("unplugged")
        def close(self): pass

    # Provision an approved friend and force the live-USB branch.
    user = auth_db.create_pending_user("offline_test", "hunter2hunter")
    auth_db.set_status(user["id"], "allowed")
    monkeypatch.setattr(cfg, "DRY_RUN", False)
    monkeypatch.setattr(printer, "Usb", _Offline)

    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.post("/api/m/print", json={"body": "hello"})
    assert r.status_code == 503
    body = r.get_json()
    assert body["ok"] is False
    assert body["kind"] == "printer"
    assert "Printer not responding" in body["error"]
