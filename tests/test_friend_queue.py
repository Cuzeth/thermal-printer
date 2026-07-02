"""Friend-message abuse caps: the cut directive is stripped, message length
is enforced, and one friend can't fill the shared print queue."""

from __future__ import annotations

import queue

import pytest

import app as app_module
from auth import db as auth_db, session as sess
from features import widgets


@pytest.fixture
def client():
    return app_module.app.test_client()


def _signed_in_client(client, name: str):
    user = auth_db.create_pending_user(name, "hunter2hunter")
    auth_db.set_status(user["id"], "allowed")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]
    return user


def test_friend_message_strips_cut_directive():
    out = widgets.friend_message("alice", "hi\n!!!\nbye")
    assert "!!!" not in out


def test_friend_preview_yields_single_segment_despite_cuts(client):
    """A body full of !!! lines must not produce one print segment per cut —
    the directive is dropped for friends."""
    _signed_in_client(client, "q_cutter")
    body = "hello\n" + "\n".join(["!!!"] * 40) + "\nbye"
    r = client.post("/api/m/preview", json={"body": body})
    assert r.status_code == 200
    assert len(r.get_json()["segments"]) == 1


def test_friend_print_rejects_oversized_body(client):
    _signed_in_client(client, "q_long")
    r = client.post("/api/m/print", json={"body": "x" * (app_module._MAX_MSG_LEN + 1)})
    assert r.status_code == 400
    assert "too long" in r.get_json()["error"]


def test_friend_print_per_user_cap(client):
    """A friend at the in-flight cap gets a 429, not another queue slot."""
    user = _signed_in_client(client, "q_flood")
    with app_module._inflight_lock:
        app_module._inflight[user["id"]] = app_module._PER_USER_QUEUE_CAP
    try:
        r = client.post("/api/m/print", json={"body": "hello"})
        assert r.status_code == 429
        assert r.get_json()["kind"] == "user_cap"
    finally:
        with app_module._inflight_lock:
            app_module._inflight.pop(user["id"], None)


def test_friend_print_queue_full_rolls_back_inflight(client, monkeypatch):
    """queue.Full → 503, and the user's in-flight count is released so they
    aren't locked out once the queue drains."""
    user = _signed_in_client(client, "q_full")

    def _raise_full(item):
        raise queue.Full

    monkeypatch.setattr(app_module._PRINT_QUEUE, "put_nowait", _raise_full)
    r = client.post("/api/m/print", json={"body": "hello"})
    assert r.status_code == 503
    assert r.get_json()["kind"] == "queue_full"
    with app_module._inflight_lock:
        assert app_module._inflight.get(user["id"], 0) == 0
