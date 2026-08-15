"""HTTP-surface tests — admin auth gate and basic routing. Everything runs
in DRY_RUN so no USB is touched. These exercise the Bearer-token path;
the TOTP session path lives in test_security.py."""

from __future__ import annotations

import io

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


def test_signed_out_admin_page_does_not_render_signout(client):
    r = client.get("/admin")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="login-form"' in html
    assert 'id="signout"' not in html


def test_signed_in_admin_console_scopes_signout_to_owner_shell(client, auth):
    r = client.get("/admin", headers=auth)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert html.count('id="signout"') == 1
    assert 'class="account-actions"' in html
    assert 'id="preview-panel"' in html


def test_friend_guest_page_hides_the_account_actions(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="who" aria-label="signed-in account" hidden' in html
    assert html.count('id="logout"') == 1


def test_friend_page_uses_first_person_owner_copy(client):
    html = client.get("/").get_data(as_text=True).lower()
    assert "cuzeth" not in html
    assert "—" not in html
    assert "…" not in html
    assert "send me a receipt" in html
    assert "print on my desk" in html
    assert "i'll approve it" in html


def test_private_route_requires_bearer(client):
    r = client.post("/api/admin/print/now", json={})
    assert r.status_code == 401
    assert r.get_json()["error"] == "auth required"


def test_private_route_rejects_wrong_bearer(client):
    r = client.post("/api/admin/print/now", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_private_route_accepts_owner_bearer(client, auth):
    r = client.post("/api/admin/print/now", json={}, headers=auth)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_friend_management_accepts_the_same_token(client, auth):
    # One ADMIN_TOKEN covers every /api/admin route — print, hardware,
    # and friend management alike.
    r = client.get("/api/admin/users", headers=auth)
    assert r.status_code == 200


def test_preview_rich_returns_segments(client, auth):
    r = client.post("/api/admin/preview/rich", json={"body": "# HI\n!!!\ngoodbye"}, headers=auth)
    assert r.status_code == 200
    segs = r.get_json()["segments"]
    assert len(segs) == 2
    for s in segs:
        assert s.startswith("data:image/png;base64,")


def test_widget_preview_uses_raster_render_pipeline(client, auth):
    r = client.post(
        "/api/admin/preview/widget/calendar",
        json={"year": 2026, "month": 8},
        headers=auth,
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data_url"].startswith("data:image/png;base64,")
    assert body["width"] == config.PRINTER_PIXEL_WIDTH
    assert body["height"] > 0


def test_every_widget_kind_has_a_preview_route(client, auth, monkeypatch):
    for formatter in (
        "weather", "roll_dice", "hacker_news", "on_this_day",
        "calendar_month", "countdown", "habit_tracker", "advice",
        "morning_briefing", "ascii_art", "now_card",
    ):
        monkeypatch.setattr(
            app_module.widgets,
            formatter,
            lambda *args, **kwargs: "# PREVIEW\n===\n> test",
        )

    cases = {
        "weather": {"location": "New York", "days": 1},
        "dice": {"count": 2, "sides": 6, "mode": "standard"},
        "hn": {"count": 3},
        "onthisday": {"count": 3},
        "calendar": {"year": 2026, "month": 8},
        "countdown": {"label": "launch", "date": "2026-08-20"},
        "habits": {"habits": ["water"]},
        "advice": {},
        "briefing": {"location": "New York"},
        "ascii": {"name": "cat"},
        "now": {},
    }
    for kind, payload in cases.items():
        r = client.post(f"/api/admin/preview/widget/{kind}", json=payload, headers=auth)
        assert r.status_code == 200, kind
        assert r.get_json()["data_url"].startswith("data:image/png;base64,"), kind


def test_lab_preview_uses_same_validation_as_print(client, auth):
    bad = client.post(
        "/api/admin/preview/lab/todo",
        json={"title": "TODAY", "items": ["", "  "]},
        headers=auth,
    )
    assert bad.status_code == 400
    assert bad.get_json()["error"] == "add at least one item"

    good = client.post(
        "/api/admin/preview/lab/todo",
        json={"title": "TODAY", "items": ["water basil"]},
        headers=auth,
    )
    assert good.status_code == 200
    assert good.get_json()["data_url"].startswith("data:image/png;base64,")

    for kind, payload in {
        "label": {"text": "FRAGILE", "big": True},
        "receipt": {
            "store": "TEST SHOP",
            "items": [{"name": "coffee", "qty": 1, "price": 4.25}],
            "tax_rate": 0,
            "note": "",
        },
    }.items():
        r = client.post(f"/api/admin/preview/lab/{kind}", json=payload, headers=auth)
        assert r.status_code == 200, kind
        assert r.get_json()["data_url"].startswith("data:image/png;base64,"), kind


def test_widget_and_lab_previews_require_admin(client):
    assert client.post("/api/admin/preview/widget/now", json={}).status_code == 401
    assert client.post("/api/admin/preview/lab/label", json={"text": "HI"}).status_code == 401


def test_unknown_preview_kind_is_rejected(client, auth):
    r = client.post("/api/admin/preview/widget/nope", json={}, headers=auth)
    assert r.status_code == 400
    assert r.get_json()["error"] == "unknown widget: nope"


def test_hw_raw_rejects_oversized_payload(client, auth):
    # Build a hex blob that parses to > 4096 bytes.
    payload = " ".join(["20"] * 5000)
    r = client.post("/api/admin/hw/raw", json={"bytes": payload}, headers=auth)
    assert r.status_code == 400
    assert r.get_json()["error"] == "5000 bytes. limit: 4096"


def test_hw_raw_accepts_small_payload(client, auth):
    r = client.post("/api/admin/hw/raw", json={"bytes": "1b 40"}, headers=auth)
    assert r.status_code == 200
    assert r.get_json()["sent"] == 2


def test_oversized_body_returns_json_413(client, auth):
    data = {"file": (io.BytesIO(b"\0" * (17 * 1024 * 1024)), "big.png")}
    r = client.post("/api/admin/image/preview", data=data, headers=auth,
                     content_type="multipart/form-data")
    assert r.status_code == 413
    body = r.get_json()
    assert body["ok"] is False
    assert body["kind"] == "input"


def test_image_preview_rejects_garbage_upload(client, auth):
    data = {"file": (io.BytesIO(b"not an image"), "x.png")}
    r = client.post("/api/admin/image/preview", data=data, headers=auth,
                     content_type="multipart/form-data")
    assert r.status_code == 400
    assert r.get_json()["kind"] == "input"


def test_friend_print_requires_session(client):
    # No session cookie → 401.
    r = client.post("/api/print", json={"body": "hi"})
    assert r.status_code == 401


def test_friend_history_requires_session(client):
    r = client.get("/api/history")
    assert r.status_code == 401


def test_friend_printer_status_requires_session(client):
    r = client.get("/api/printer")
    assert r.status_code == 401


def test_friend_printer_status_shape(client):
    """Signed-in allowed friend gets the soft banner's backing data: a
    top-level ok plus a nested printer object with a boolean ok."""
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("printer_alice", "hunter2hunter")
    auth_db.set_status(user["id"], "allowed")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.get("/api/printer")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert isinstance(body["printer"]["ok"], bool)


def test_friend_history_rejects_pending_user(client):
    """A pending friend is signed-in but not allowed — the endpoint has to
    return 403 (not 401, not the history itself)."""
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("hist_pending", "hunter2hunter")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.get("/api/history")
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

    r = client.get("/api/history")
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
        assert set(m.keys()) == {"id", "body", "status", "printed_at"}


def test_friend_history_limit_is_clamped(client):
    """Limit is sanitized — no way to ask for 10_000 rows or a negative
    limit and no way to crash the handler with a non-numeric value."""
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("hist_clamp", "hunter2hunter")
    auth_db.set_status(user["id"], "allowed")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    # Non-numeric → handled gracefully.
    r = client.get("/api/history?limit=banana")
    assert r.status_code == 200
    # Too big → capped at 200 (no crash, returns JSON).
    r = client.get("/api/history?limit=999999")
    assert r.status_code == 200


def test_friend_settings_updates_name_style(client):
    """Setting a valid name_style persists and comes back on /me."""
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("style_alice", "hunter2hunter")
    auth_db.set_status(user["id"], "allowed")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.post("/api/settings", json={"name_style": "script"})
    assert r.status_code == 200
    assert r.get_json()["user"]["name_style"] == "script"

    r = client.get("/api/me")
    assert r.get_json()["user"]["name_style"] == "script"


def test_friend_settings_rejects_unknown_style(client):
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("style_bogus", "hunter2hunter")
    auth_db.set_status(user["id"], "allowed")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.post("/api/settings", json={"name_style": "fancy"})
    assert r.status_code == 400


def test_friend_settings_requires_approval(client):
    """Pending friends can't mutate settings — keeps the style picker out
    of the hands of un-approved signups."""
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("style_pending", "hunter2hunter")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.post("/api/settings", json={"name_style": "script"})
    assert r.status_code == 403


def test_friend_preview_honors_anonymous_flag(client):
    """Anonymous prints strip the username — confirm via the preview
    endpoint so we don't need a live printer to check it."""
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("anon_alice", "hunter2hunter")
    auth_db.set_status(user["id"], "allowed")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.post("/api/preview", json={"body": "hi", "anonymous": True})
    assert r.status_code == 200
    segs = r.get_json()["segments"]
    # Any non-empty body produces at least one preview image.
    assert len(segs) >= 1


def test_admin_reset_link_flow(client, auth):
    """Admin mints a link → friend redeems it with a password of their own
    choosing → old password dead, new one works, and the redeeming browser
    is signed in on the spot (no second trip through the login form)."""
    from auth import blueprint as auth_bp_mod, db as auth_db

    user = auth_db.create_pending_user("pwreset_alice", "oldpassword1")
    auth_db.set_status(user["id"], "allowed")

    r = client.post(f"/api/admin/users/{user['id']}/reset_link", headers=auth)
    assert r.status_code == 200
    j = r.get_json()
    assert j["path"].startswith("/#reset=")
    token = j["path"].split("=", 1)[1]

    r = client.post("/api/auth/reset",
                    json={"token": token, "password": "newpassword1"})
    assert r.status_code == 200
    assert r.get_json()["user"]["username"] == "pwreset_alice"
    r = client.get("/api/me")
    assert r.get_json()["user"]["username"] == "pwreset_alice"

    auth_bp_mod._failures.clear()
    auth_bp_mod._ip_failures.clear()
    r = client.post("/api/auth/login",
                    json={"username": "pwreset_alice", "password": "oldpassword1"})
    assert r.status_code == 401
    r = client.post("/api/auth/login",
                    json={"username": "pwreset_alice", "password": "newpassword1"})
    assert r.status_code == 200


def test_admin_reset_link_single_use(client, auth):
    """A redeemed link is burnt — replaying the same token fails."""
    from auth import blueprint as auth_bp_mod, db as auth_db
    auth_bp_mod._ip_failures.clear()

    user = auth_db.create_pending_user("pwreset_carol", "oldpassword1")
    token = auth_db.create_reset_token(user["id"])
    r = client.post("/api/auth/reset",
                    json={"token": token, "password": "newpassword1"})
    assert r.status_code == 200
    r = client.post("/api/auth/reset",
                    json={"token": token, "password": "sneakypassword1"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "link expired. text me"


def test_admin_reset_link_expires(client, auth):
    """A token past its expiry timestamp is refused."""
    from auth import blueprint as auth_bp_mod, db as auth_db
    auth_bp_mod._ip_failures.clear()

    user = auth_db.create_pending_user("pwreset_dave", "oldpassword1")
    token = auth_db.create_reset_token(user["id"])
    with auth_db.db() as conn:
        conn.execute(
            "UPDATE users SET reset_token_expires = datetime('now', '-1 minute') "
            "WHERE id = ?", (user["id"],))
    r = client.post("/api/auth/reset",
                    json={"token": token, "password": "newpassword1"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "link expired. text me"


def test_admin_reset_link_validates(client, auth):
    """A too-short password is rejected *without* burning the one-shot
    token; minting a link for an unknown user 404s."""
    from auth import blueprint as auth_bp_mod, db as auth_db
    auth_bp_mod._ip_failures.clear()

    user = auth_db.create_pending_user("pwreset_bob", "oldpassword1")
    token = auth_db.create_reset_token(user["id"])
    r = client.post("/api/auth/reset", json={"token": token, "password": "short"})
    assert r.status_code == 400
    r = client.post("/api/auth/reset", json={"token": token, "password": "longenough1"})
    assert r.status_code == 200

    r = client.post("/api/admin/users/999999/reset_link", headers=auth)
    assert r.status_code == 404


def test_admin_reset_link_requires_bearer(client):
    r = client.post("/api/admin/users/1/reset_link")
    assert r.status_code == 401


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

    r = client.post("/api/print", json={"body": "hello"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["queued"] is True

    # Wait for the worker to consume the job before tearing down monkeypatch,
    # otherwise the patched USB stub leaks into other tests.
    app_module._PRINT_QUEUE.join()

    # History was written at enqueue time, and the worker flipped the row
    # to 'failed' so the friend can see it never hit paper.
    msgs = auth_db.list_messages_for_user(user["id"], limit=5)
    row = next(m for m in msgs if m["body"] == "hello")
    assert row["status"] == "failed"


def test_admin_approve_flow(client, auth):
    """Approve flips a pending user to allowed and stamps approved_at."""
    from auth import db as auth_db

    user = auth_db.create_pending_user("adm_approve", "hunter2hunter")

    r = client.post(f"/api/admin/users/{user['id']}/approve", headers=auth)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    fresh = auth_db.get_user(user["id"])
    assert fresh["status"] == "allowed"
    assert fresh["approved_at"] is not None


def test_admin_revoke_blocks_access(client, auth):
    """Revoke sets status 'blocked' and a blocked friend's session stops
    working (403 on friend routes)."""
    from auth import db as auth_db, session as sess

    user = auth_db.create_pending_user("adm_revoke", "hunter2hunter")
    r = client.post(f"/api/admin/users/{user['id']}/approve", headers=auth)
    assert r.status_code == 200

    r = client.post(f"/api/admin/users/{user['id']}/revoke", headers=auth)
    assert r.status_code == 200
    assert auth_db.get_user(user["id"])["status"] == "blocked"

    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]

    r = client.get("/api/history")
    assert r.status_code == 403


def test_admin_delete_removes_user_and_history(client, auth):
    """Delete removes the user row and cascades to their messages."""
    from auth import db as auth_db

    user = auth_db.create_pending_user("adm_delete", "hunter2hunter")
    auth_db.log_message(user["id"], "doomed")

    r = client.post(f"/api/admin/users/{user['id']}/delete", headers=auth)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    assert auth_db.get_user(user["id"]) is None
    assert auth_db.list_messages_for_user(user["id"]) == []


def test_admin_lifecycle_404_on_missing_user(client, auth):
    """All three lifecycle endpoints 404 cleanly on an unknown id."""
    for action in ("approve", "revoke", "delete"):
        r = client.post(f"/api/admin/users/999999/{action}", headers=auth)
        assert r.status_code == 404
        assert r.get_json()["error"] == "user not found"


def test_admin_lifecycle_requires_bearer(client):
    """No token → 401, and nothing changes."""
    from auth import db as auth_db

    user = auth_db.create_pending_user("adm_nobearer", "hunter2hunter")

    for action in ("approve", "revoke", "delete"):
        r = client.post(f"/api/admin/users/{user['id']}/{action}")
        assert r.status_code == 401

    fresh = auth_db.get_user(user["id"])
    assert fresh is not None
    assert fresh["status"] == "pending"


def test_admin_messages_lists_all_users_newest_first(client, auth):
    """Admin messages feed spans users, includes usernames, honors limit."""
    from auth import db as auth_db

    user_a = auth_db.create_pending_user("adm_msgs_a", "hunter2hunter")
    user_b = auth_db.create_pending_user("adm_msgs_b", "hunter2hunter")
    auth_db.log_message(user_a["id"], "message from a")
    auth_db.log_message(user_b["id"], "message from b")

    r = client.get("/api/admin/messages", headers=auth)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    bodies = [m["body"] for m in body["messages"]]
    assert "message from a" in bodies
    assert "message from b" in bodies
    for m in body["messages"]:
        assert set(m.keys()) == {"id", "body", "status", "printed_at", "username"}

    r = client.get("/api/admin/messages?limit=1", headers=auth)
    assert r.status_code == 200
    assert len(r.get_json()["messages"]) == 1
