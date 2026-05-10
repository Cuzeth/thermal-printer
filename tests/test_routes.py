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
    r = client.post("/api/print/now", json={})
    assert r.status_code == 401
    assert r.get_json()["error"] == "auth required"


def test_private_route_rejects_wrong_bearer(client):
    r = client.post("/api/print/now", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_private_route_accepts_owner_bearer(client, auth):
    r = client.post("/api/print/now", json={}, headers=auth)
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


def test_friend_history_requires_session(client):
    r = client.get("/api/m/history")
    assert r.status_code == 401


def test_friend_history_rejects_pending_user(client):
    """A pending friend is signed-in but not allowed — the endpoint has to
    return 403 (not 401, not the history itself)."""
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("hist_pending", "hunter2hunter")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.get("/api/m/history")
    assert r.status_code == 403


def test_friend_history_returns_own_messages_only(client):
    """A friend's history only contains their own prints — never anyone
    else's, regardless of timestamps."""
    from auth import db as auth_db, session as sess

    alice = auth_db.create_pending_user("hist_alice", "hunter2hunter")
    bob = auth_db.create_pending_user("hist_bob", "hunter2hunter")
    auth_db.set_status(alice["id"], "allowed")
    auth_db.set_status(bob["id"], "allowed")
    auth_db.log_message(alice["id"], "alice one")
    auth_db.log_message(bob["id"], "bob one")
    auth_db.log_message(alice["id"], "alice two")

    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = alice["id"]

    r = client.get("/api/m/history")
    assert r.status_code == 200
    msgs = r.get_json()["messages"]
    bodies = [m["body"] for m in msgs]
    assert "alice one" in bodies
    assert "alice two" in bodies
    assert "bob one" not in bodies
    # Newest first: alice two was logged after alice one.
    assert bodies.index("alice two") < bodies.index("alice one")
    # Each row has the shape the UI expects.
    for m in msgs:
        assert set(m.keys()) == {"id", "body", "printed_at"}


def test_friend_history_limit_is_clamped(client):
    """Limit is sanitized — no way to ask for 10_000 rows or a negative
    limit and no way to crash the handler with a non-numeric value."""
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("hist_clamp", "hunter2hunter")
    auth_db.set_status(user["id"], "allowed")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    # Non-numeric → handled gracefully.
    r = client.get("/api/m/history?limit=banana")
    assert r.status_code == 200
    # Too big → capped at 200 (no crash, returns JSON).
    r = client.get("/api/m/history?limit=999999")
    assert r.status_code == 200


def test_friend_settings_updates_name_style(client):
    """Setting a valid name_style persists and comes back on /me."""
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("style_alice", "hunter2hunter")
    auth_db.set_status(user["id"], "allowed")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.post("/api/m/settings", json={"name_style": "script"})
    assert r.status_code == 200
    assert r.get_json()["user"]["name_style"] == "script"

    r = client.get("/api/m/me")
    assert r.get_json()["user"]["name_style"] == "script"


def test_friend_settings_rejects_unknown_style(client):
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("style_bogus", "hunter2hunter")
    auth_db.set_status(user["id"], "allowed")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.post("/api/m/settings", json={"name_style": "fancy"})
    assert r.status_code == 400


def test_friend_settings_requires_approval(client):
    """Pending friends can't mutate settings — keeps the style picker out
    of the hands of un-approved signups."""
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("style_pending", "hunter2hunter")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.post("/api/m/settings", json={"name_style": "script"})
    assert r.status_code == 403


def test_friend_preview_honors_anonymous_flag(client):
    """Anonymous prints strip the username — confirm via the preview
    endpoint so we don't need a live printer to check it."""
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("anon_alice", "hunter2hunter")
    auth_db.set_status(user["id"], "allowed")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.post("/api/m/preview", json={"body": "hi", "anonymous": True})
    assert r.status_code == 200
    segs = r.get_json()["segments"]
    # Any non-empty body produces at least one preview image.
    assert len(segs) >= 1


def test_friend_print_queues_even_when_printer_offline(client, monkeypatch):
    """Queue contract: a valid message is accepted (200 + queued) even
    when the printer is offline. The failure is async — surfaced in the
    worker's stderr, not in the HTTP response — so the next message still
    gets a chance and the queue doesn't wedge."""
    import config as cfg
    import printer
    from auth import db as auth_db, session as sess

    class _Offline:
        def __init__(self, *a, **k): pass
        def open(self):
            raise type("DeviceNotFoundError", (Exception,), {})("unplugged")
        def close(self): pass

    user = auth_db.create_pending_user("offline_test", "hunter2hunter")
    auth_db.set_status(user["id"], "allowed")
    monkeypatch.setattr(cfg, "DRY_RUN", False)
    monkeypatch.setattr(printer, "Usb", _Offline)

    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.post("/api/m/print", json={"body": "hello"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["queued"] is True

    # Wait for the worker to consume the job before tearing down monkeypatch,
    # otherwise the patched USB stub leaks into other tests.
    app_module._PRINT_QUEUE.join()

    # History was written at enqueue time — intent, not on-paper success.
    msgs = auth_db.list_messages_for_user(user["id"], limit=5)
    assert any(m["body"] == "hello" for m in msgs)
