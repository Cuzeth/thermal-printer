"""Owner troubleshooting keeps surprises out of lists and behind explicit reveals."""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

import app as app_module
import config
from auth import db as auth_db, session as sess


@pytest.fixture
def client(monkeypatch, tmp_path):
    app_module._PRINT_QUEUE.join()
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "print-log.db")
    auth_db.init()
    return app_module.app.test_client()


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {config.ADMIN_TOKEN}"}


def friend():
    user = auth_db.create_pending_user("surprise_sender", "surprise-password")
    auth_db.set_status(user["id"], "allowed")
    return user


def test_log_excludes_undelivered_and_never_prefetches_contents(client, auth):
    """Even explicit inclusion returns only metadata, with no content or schedule."""
    user = friend()
    ids = {status: auth_db.log_message(user["id"], "secret birthday message", status=status)
           for status in ("printed", "failed", "scheduled", "queued", "cancelled")}
    for query, expected in (("", [ids["failed"], ids["printed"]]),
                            ("?include_undelivered=true", [ids["failed"], ids["printed"]]),
                            ("?include_undelivered=1", list(reversed(ids.values())))):
        response = client.get("/api/admin/messages" + query, headers=auth)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        messages = response.get_json()["messages"]
        assert [message["id"] for message in messages] == expected
        assert all(set(message) == {"id", "kind", "status", "printed_at"} for message in messages)
        assert "secret birthday message" not in response.get_data(as_text=True)
        assert user["username"] not in response.get_data(as_text=True)


def test_failed_feed_finds_older_failures_without_revealing_other_jobs(client, auth):
    """Newer successful or scheduled jobs cannot push an unresolved failure out."""
    user = friend()
    failed_id = auth_db.log_message(user["id"], "secret failed print", status="failed")
    for _ in range(25):
        auth_db.log_message(user["id"], "printed already")
    for status in ("scheduled", "queued", "cancelled"):
        auth_db.log_message(user["id"], "future surprise", status=status)
    for suffix in ("", "&include_undelivered=1"):
        response = client.get("/api/admin/messages?status=failed&limit=1" + suffix, headers=auth)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.get_json()["messages"] == [{
            "id": failed_id, "kind": "text", "status": "failed",
            "printed_at": auth_db.get_message(failed_id)["printed_at"],
        }]


def test_failed_feed_tracks_unsuccessful_then_successful_retry(client, auth, monkeypatch):
    """A failed retry stays actionable; a successful retry clears the visible list."""
    from printer import PrinterError

    msg_id = auth_db.log_message(friend()["id"], "secret receipt", status="failed")
    printed = []

    def offline(message):
        raise PrinterError("printer offline")

    monkeypatch.setattr(app_module, "_print_friend_message", offline)
    response = client.post(f"/api/admin/messages/{msg_id}/retry", headers=auth)
    assert response.status_code == 503
    messages = client.get("/api/admin/messages?status=failed", headers=auth).get_json()["messages"]
    assert [message["id"] for message in messages] == [msg_id]
    monkeypatch.setattr(app_module, "_print_friend_message", lambda message: printed.append(message["body"]))
    response = client.post(f"/api/admin/messages/{msg_id}/retry", headers=auth)
    assert response.status_code == 200
    assert "secret receipt" not in response.get_data(as_text=True)
    assert printed == ["secret receipt"]
    assert client.get("/api/admin/messages?status=failed", headers=auth).get_json()["messages"] == []


@pytest.mark.parametrize("status", ["scheduled", "queued", "cancelled"])
def test_undelivered_reveal_requires_explicit_boolean_opt_in(client, auth, status):
    """Knowing an id cannot accidentally reveal a print that has not arrived."""
    msg_id = auth_db.log_message(friend()["id"], "future surprise", status=status)
    url = f"/api/admin/messages/{msg_id}/reveal"
    for data in ({}, {"include_undelivered": False}, {"include_undelivered": "true"},
                 {"include_undelivered": 1}):
        response = client.post(url, json=data, headers=auth)
        assert response.status_code == 400
        assert response.headers["Cache-Control"] == "no-store"
        assert "future surprise" not in response.get_data(as_text=True)
    response = client.post(url, json={"include_undelivered": True}, headers=auth)
    assert response.status_code == 200
    assert response.get_json()["message"]["body"] == "future surprise"
    # The opt-in is per request, never a persistent setting on the session.
    assert client.post(url, json={}, headers=auth).status_code == 400
    auth_db.set_message_status(msg_id, "printed")
    assert client.post(url, json={}, headers=auth).status_code == 200


@pytest.mark.parametrize("status", ["printed", "failed"])
def test_individual_reveal_preserves_text_and_sender(client, auth, status):
    """Troubleshooting still exposes the exact selected receipt when requested."""
    user = friend()
    msg_id = auth_db.log_message(user["id"], "**keep this exact**", status=status, anonymous=True)
    other_id = auth_db.log_message(user["id"], "another surprise")
    response = client.post(f"/api/admin/messages/{msg_id}/reveal", json={}, headers=auth)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["message"] == {
        "id": msg_id, "body": "**keep this exact**", "status": status, "kind": "text",
        "username": user["username"], "anonymous": True, "image": None,
        "printed_at": auth_db.get_message(msg_id)["printed_at"], "deliver_at": None,
    }
    assert "another surprise" not in response.get_data(as_text=True)
    assert auth_db.get_message(other_id)["status"] == "printed"


@pytest.mark.parametrize("kind", ["doodle", "photo"])
def test_images_are_available_only_on_individual_reveal(client, auth, kind):
    """Saved image receipts remain inspectable without including images in lists."""
    data = io.BytesIO()
    Image.new("RGB", (24, 24), "white").save(data, "PNG")
    image = data.getvalue()
    msg_id = auth_db.log_message(friend()["id"], "caption", drawing=image, kind=kind)
    listing = client.get("/api/admin/messages", headers=auth).get_json()["messages"]
    assert "image" not in listing[0] and "drawing" not in listing[0]
    message = client.post(f"/api/admin/messages/{msg_id}/reveal", json={}, headers=auth).get_json()["message"]
    assert message["kind"] == kind
    assert message["image"] == "data:image/png;base64," + base64.b64encode(image).decode("ascii")


def test_log_and_reveal_require_owner_auth_not_friend_session(client):
    """Neither anonymous visitors nor approved friends gain owner history access."""
    user = friend()
    msg_id = auth_db.log_message(user["id"], "my own print")
    for signed_in in (False, True):
        if signed_in:
            with client.session_transaction() as session:
                session[sess.SESSION_USER_KEY] = user["id"]
        assert client.get("/api/admin/messages?include_undelivered=1").status_code == 401
        assert client.post(f"/api/admin/messages/{msg_id}/reveal",
                           json={"include_undelivered": True}).status_code == 401


def test_reveal_rejects_missing_rows_and_malformed_requests(client, auth):
    """Malformed requests cannot produce content or a server error."""
    msg_id = auth_db.log_message(friend()["id"], "stay concealed")
    url = f"/api/admin/messages/{msg_id}/reveal"
    for payload in ("{", "null", "[]", '"reveal"'):
        response = client.post(url, data=payload, content_type="application/json", headers=auth)
        assert response.status_code == 400
        assert "stay concealed" not in response.get_data(as_text=True)
    assert client.post("/api/admin/messages/999999/reveal", json={}, headers=auth).status_code == 400
    assert client.get(url, headers=auth).status_code == 405
