"""Friend-message abuse caps: the cut directive is stripped, message length
is enforced, and one friend can't fill the shared print queue."""

from __future__ import annotations

import base64
import io
import queue
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import app as app_module
import config
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


def _doodle_data_url(blank: bool = False) -> str:
    img = Image.new("RGB", (576, 576), (255, 255, 255))
    if not blank:
        ImageDraw.Draw(img).rectangle([100, 100, 300, 300], fill=(0, 0, 0))
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_friend_message_strips_cut_directive():
    out = widgets.friend_message("alice", "hi\n!!!\nbye")
    assert "!!!" not in out


def test_friend_preview_yields_single_segment_despite_cuts(client):
    """A body full of !!! lines must not produce one print segment per cut —
    the directive is dropped for friends."""
    _signed_in_client(client, "q_cutter")
    body = "hello\n" + "\n".join(["!!!"] * 40) + "\nbye"
    r = client.post("/api/preview", json={"body": body})
    assert r.status_code == 200
    assert len(r.get_json()["segments"]) == 1


def test_friend_print_rejects_oversized_body(client):
    _signed_in_client(client, "q_long")
    r = client.post("/api/print", json={"body": "x" * (app_module._MAX_MSG_LEN + 1)})
    assert r.status_code == 400
    assert r.get_json()["error"] == "800 character limit"


def test_friend_print_per_user_cap(client):
    """A friend at the in-flight cap gets a 429, not another queue slot."""
    user = _signed_in_client(client, "q_flood")
    with app_module._inflight_lock:
        app_module._inflight[user["id"]] = app_module._PER_USER_QUEUE_CAP
    try:
        r = client.post("/api/print", json={"body": "hello"})
        assert r.status_code == 429
        assert r.get_json()["kind"] == "user_cap"
    finally:
        with app_module._inflight_lock:
            app_module._inflight.pop(user["id"], None)


def test_friend_print_marks_status_printed_on_success(client):
    """DRY_RUN print succeeds → the worker flips the history row from
    'queued' to 'printed'."""
    user = _signed_in_client(client, "q_status")
    r = client.post("/api/print", json={"body": "status check"})
    assert r.status_code == 200
    app_module._PRINT_QUEUE.join()  # worker updates status before task_done()
    msgs = auth_db.list_messages_for_user(user["id"], limit=5)
    row = next(m for m in msgs if m["body"] == "status check")
    assert row["status"] == "printed"


def test_friend_print_queue_full_rolls_back_inflight(client, monkeypatch):
    """queue.Full → 503, and the user's in-flight count is released so they
    aren't locked out once the queue drains."""
    user = _signed_in_client(client, "q_full")

    def _raise_full(item):
        raise queue.Full

    monkeypatch.setattr(app_module._PRINT_QUEUE, "put_nowait", _raise_full)
    r = client.post("/api/print", json={"body": "hello"})
    assert r.status_code == 503
    assert r.get_json()["kind"] == "queue_full"
    with app_module._inflight_lock:
        assert app_module._inflight.get(user["id"], 0) == 0
    # The optimistic history row is removed — the job never entered the queue.
    msgs = auth_db.list_messages_for_user(user["id"], limit=5)
    assert not any(m["body"] == "hello" for m in msgs)


def test_friend_print_db_failure_rolls_back_inflight(client, monkeypatch):
    """A log_message crash returns JSON 500 (not Flask's HTML 500 page) and
    does not eat a cap slot — the friend can retry immediately."""
    user = _signed_in_client(client, "q_dbfail")

    def _raise(*a, **kw):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(auth_db, "log_message", _raise)
    r = client.post("/api/print", json={"body": "hello"})
    assert r.status_code == 500
    body = r.get_json()
    assert body["ok"] is False
    assert body["kind"] == "server"
    with app_module._inflight_lock:
        assert app_module._inflight.get(user["id"], 0) == 0

    monkeypatch.undo()
    r2 = client.post("/api/print", json={"body": "hello again"})
    assert r2.status_code == 200
    assert r2.get_json()["queued"] is True
    app_module._PRINT_QUEUE.join()


def test_worker_survives_status_update_failure(client, monkeypatch):
    """A DB error while flipping queued -> printed must not kill the worker
    thread; the next job still processes and decrements in-flight."""
    user = _signed_in_client(client, "q_statusfail")

    def _raise(*a, **kw):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(auth_db, "set_message_status", _raise)
    r = client.post("/api/print", json={"body": "first"})
    assert r.status_code == 200
    app_module._PRINT_QUEUE.join()

    monkeypatch.undo()
    r2 = client.post("/api/print", json={"body": "second"})
    assert r2.status_code == 200
    app_module._PRINT_QUEUE.join()

    msgs = auth_db.list_messages_for_user(user["id"], limit=5)
    row = next(m for m in msgs if m["body"] == "second")
    assert row["status"] == "printed"
    with app_module._inflight_lock:
        assert app_module._inflight.get(user["id"], 0) == 0


def test_boot_replays_orphaned_queued_rows(monkeypatch):
    """Rows left 'queued' by a process that died are picked back up at
    boot and printed, oldest first, with the friend's original send time
    on the footer — not dropped or failed."""
    user = auth_db.create_pending_user("q_orphan", "hunter2hunter")
    first = auth_db.log_message(user["id"], "orphan one", status="queued")
    second = auth_db.log_message(user["id"], "orphan two", status="queued")
    seen = []
    monkeypatch.setattr(app_module, "_print_body", lambda body, **kw: seen.append(body))

    # Other tests may leave their own 'queued' rows behind; only ours
    # are asserted on.
    assert app_module._replay_queued() >= 2
    app_module._PRINT_QUEUE.join()

    ours = [b for b in seen if "orphan" in b]
    assert len(ours) == 2
    assert "orphan one" in ours[0] and "orphan two" in ours[1]
    sent = auth_db.get_message(first)["printed_at"]
    stamp = (datetime.fromisoformat(sent).replace(tzinfo=timezone.utc)
             .astimezone().strftime("%I:%M %p").lower())
    assert stamp in ours[0]
    for msg_id in (first, second):
        assert auth_db.get_message(msg_id)["status"] == "printed"
    with app_module._inflight_lock:
        assert app_module._inflight.get(user["id"], 0) == 0


def test_replay_fails_rows_past_the_queue_cap(monkeypatch):
    """If more rows are queued than the in-memory queue holds, the
    overflow is marked failed (retryable) rather than left queued forever."""
    monkeypatch.setattr(app_module, "_print_body", lambda body, **kw: None)
    # Clear any 'queued' leftovers from earlier tests so the cap below
    # is exercised by our two rows alone.
    app_module._replay_queued()
    app_module._PRINT_QUEUE.join()

    user = auth_db.create_pending_user("q_overflow", "hunter2hunter")
    kept = auth_db.log_message(user["id"], "fits", status="queued")
    dropped = auth_db.log_message(user["id"], "doesn't", status="queued")
    monkeypatch.setattr(app_module, "_PRINT_QUEUE", queue.Queue(maxsize=1))

    assert app_module._replay_queued() == 1
    # Drain by hand: the worker thread is bound to the real queue object.
    uid, mid = app_module._PRINT_QUEUE.get_nowait()
    assert mid == kept
    assert auth_db.get_message(dropped)["status"] == "failed"
    app_module._dec_inflight(uid)


def test_worker_skips_rows_deleted_while_queued(client):
    """A friend removed while their print waits (cascade deletes the row)
    must not wedge the worker — it logs and moves on."""
    user = _signed_in_client(client, "q_vanish")
    msg_id = auth_db.log_message(user["id"], "gone", status="queued")
    auth_db.delete_message(msg_id)
    with app_module._inflight_lock:
        app_module._inflight[user["id"]] = 1
    app_module._PRINT_QUEUE.put((user["id"], msg_id))
    app_module._PRINT_QUEUE.join()
    with app_module._inflight_lock:
        assert app_module._inflight.get(user["id"], 0) == 0


def test_init_migrates_existing_history_for_saved_drawings(tmp_path, monkeypatch):
    """A Pi upgrading in place gets the nullable drawing column without
    losing its existing message rows."""
    old_db = tmp_path / "old-app.db"
    with sqlite3.connect(old_db) as conn:
        conn.execute(
            "CREATE TABLE messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'printed', "
            "printed_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        conn.execute(
            "INSERT INTO messages (user_id, body) VALUES (?, ?)",
            (1, "still here"),
        )

    monkeypatch.setattr(auth_db.config, "DB_PATH", old_db)
    auth_db.init()

    with sqlite3.connect(old_db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        body = conn.execute("SELECT body FROM messages").fetchone()[0]
    assert "drawing" in columns
    assert body == "still here"


# ---------- doodles ----------

def test_doodle_prints_and_lands_in_history(client):
    """A real doodle prints and keeps a reusable normalized PNG in the
    friend's history instead of only a placeholder body."""
    user = _signed_in_client(client, "q_doodle")
    r = client.post("/api/print/doodle", json={"image": _doodle_data_url()})
    assert r.status_code == 200
    assert r.get_json()["queued"] is True
    app_module._PRINT_QUEUE.join()
    msgs = auth_db.list_messages_for_user(user["id"], limit=5)
    row = next(m for m in msgs if m["body"] == "(doodle)")
    assert row["status"] == "printed"
    assert row["has_drawing"] is True

    saved = client.get(f"/api/history/{row['id']}/drawing")
    assert saved.status_code == 200
    data_url = saved.get_json()["image"]
    assert data_url.startswith("data:image/png;base64,")
    restored = Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))
    assert restored.size == (576, 576)
    assert restored.convert("L").getextrema()[0] == 0


def test_saved_doodle_is_scoped_to_its_friend(client):
    """History ids are not capabilities: a different signed-in friend
    cannot fetch someone else's saved drawing."""
    owner = _signed_in_client(client, "q_doodle_owner")
    r = client.post("/api/print/doodle", json={"image": _doodle_data_url()})
    assert r.status_code == 200
    app_module._PRINT_QUEUE.join()
    row = next(
        m for m in auth_db.list_messages_for_user(owner["id"], limit=5)
        if m["has_drawing"]
    )

    other = auth_db.create_pending_user("q_doodle_other", "hunter2hunter")
    auth_db.set_status(other["id"], "allowed")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = other["id"]

    hidden = client.get(f"/api/history/{row['id']}/drawing")
    assert hidden.status_code == 404
    assert hidden.get_json()["error"] == "drawing not found"


def test_doodle_rejects_blank_canvas(client):
    """An untouched canvas thresholds to pure white — nothing to print."""
    _signed_in_client(client, "q_doodle_blank")
    r = client.post("/api/print/doodle", json={"image": _doodle_data_url(blank=True)})
    assert r.status_code == 400
    assert "draw" in r.get_json()["error"]


def test_doodle_rejects_garbage_payloads(client):
    """Missing image, non-data-url strings, and bad base64 all come back
    as a clean 400 input error instead of a 500."""
    _signed_in_client(client, "q_doodle_garbage")
    for payload in (
        {},
        {"image": "hello"},
        {"image": "data:image/png;base64,@@@"},
    ):
        r = client.post("/api/print/doodle", json=payload)
        assert r.status_code == 400
        assert r.get_json()["kind"] == "input"


def test_doodle_requires_approval(client):
    """A pending (not yet approved) user can't print a doodle either."""
    user = auth_db.create_pending_user("q_doodle_pending", "hunter2hunter")
    with client.session_transaction() as s:
        s[sess.SESSION_USER_KEY] = user["id"]
    r = client.post("/api/print/doodle", json={"image": _doodle_data_url()})
    assert r.status_code == 403


def test_doodle_counts_against_user_cap(client):
    """A friend already at the in-flight cap gets a 429 from the doodle
    route too — it shares the same enqueue bookkeeping as text prints."""
    user = _signed_in_client(client, "q_doodle_flood")
    with app_module._inflight_lock:
        app_module._inflight[user["id"]] = app_module._PER_USER_QUEUE_CAP
    try:
        r = client.post("/api/print/doodle", json={"image": _doodle_data_url()})
        assert r.status_code == 429
        assert r.get_json()["kind"] == "user_cap"
    finally:
        with app_module._inflight_lock:
            app_module._inflight.pop(user["id"], None)


# ---------- owner retry ----------

def _admin():
    import config
    return {"Authorization": f"Bearer {config.ADMIN_TOKEN}"}


def _failed_text(client, name: str, body: str, anonymous: bool = False) -> tuple[dict, int]:
    """Enqueue a text print, wait for the worker, then mark it failed —
    the state the owner's retry button acts on."""
    user = _signed_in_client(client, name)
    r = client.post("/api/print", json={"body": body, "anonymous": anonymous})
    assert r.status_code == 200
    app_module._PRINT_QUEUE.join()
    msg_id = next(
        m["id"] for m in auth_db.list_messages_for_user(user["id"], limit=5)
        if m["body"] == body
    )
    auth_db.set_message_status(msg_id, "failed")
    return user, msg_id


def test_retry_reprints_failed_text_and_flips_status(client, monkeypatch):
    """Retrying a failed text row prints the friend's message as the
    friend route would have (name + style header) and marks it printed."""
    user, msg_id = _failed_text(client, "q_retry_text", "try again")
    auth_db.set_name_style(user["id"], "caps")
    seen = []
    monkeypatch.setattr(app_module, "_print_body", lambda body, **kw: seen.append(body))

    r = client.post(f"/api/admin/messages/{msg_id}/retry", headers=_admin())
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    sent = (datetime.fromisoformat(auth_db.get_message(msg_id)["printed_at"])
            .replace(tzinfo=timezone.utc).astimezone())
    assert seen == [widgets.friend_message(
        "q_retry_text", "try again", style="caps", when=sent)]
    row = next(m for m in auth_db.list_messages_for_user(user["id"], limit=5)
               if m["id"] == msg_id)
    assert row["status"] == "printed"


def test_retry_keeps_anonymous_choice(client, monkeypatch):
    """A friend who sent anonymously stays anonymous on the reprint — the
    flag is persisted on the row, not re-derived from the request."""
    _, msg_id = _failed_text(client, "q_retry_anon", "who dis", anonymous=True)
    seen = []
    monkeypatch.setattr(app_module, "_print_body", lambda body, **kw: seen.append(body))

    r = client.post(f"/api/admin/messages/{msg_id}/retry", headers=_admin())
    assert r.status_code == 200
    assert "anonymous" in seen[0]
    assert "q_retry_anon" not in seen[0]


def test_retry_reprints_failed_doodle_from_saved_drawing(client):
    """A doodle retry sends the same printer bytes as the original queued
    print, including its saved drawing, name header, timestamp, and cut."""
    user = _signed_in_client(client, "q_retry_doodle")
    r = client.post("/api/print/doodle", json={"image": _doodle_data_url()})
    assert r.status_code == 200
    app_module._PRINT_QUEUE.join()
    output = Path(config.DRY_RUN_PATH)
    original = output.read_bytes()
    assert original
    output.write_bytes(b"")
    msg_id = next(m["id"] for m in auth_db.list_messages_for_user(user["id"], limit=5)
                  if m["body"] == "(doodle)")
    auth_db.set_message_status(msg_id, "failed")

    r = client.post(f"/api/admin/messages/{msg_id}/retry", headers=_admin())
    assert r.status_code == 200
    assert output.read_bytes() == original
    row = next(m for m in auth_db.list_messages_for_user(user["id"], limit=5)
               if m["id"] == msg_id)
    assert row["status"] == "printed"


def test_retry_stays_failed_when_printer_is_offline(client, monkeypatch):
    """A retry that hits an offline printer answers 503 in the printer
    shape and leaves the row failed so the button is still there."""
    from printer import PrinterError
    user, msg_id = _failed_text(client, "q_retry_offline", "still broken")

    def _offline(body, **kw):
        raise PrinterError("printer offline")

    monkeypatch.setattr(app_module, "_print_body", _offline)
    r = client.post(f"/api/admin/messages/{msg_id}/retry", headers=_admin())
    assert r.status_code == 503
    assert r.get_json()["kind"] == "printer"
    row = next(m for m in auth_db.list_messages_for_user(user["id"], limit=5)
               if m["id"] == msg_id)
    assert row["status"] == "failed"


def test_retry_refuses_rows_that_did_print(client):
    """Retry is for failures only — a printed row is rejected as input,
    and an unknown id looks the same."""
    user = _signed_in_client(client, "q_retry_printed")
    msg_id = auth_db.log_message(user["id"], "went fine")
    r = client.post(f"/api/admin/messages/{msg_id}/retry", headers=_admin())
    assert r.status_code == 400
    assert r.get_json()["kind"] == "input"
    r = client.post("/api/admin/messages/999999/retry", headers=_admin())
    assert r.status_code == 400


def test_retry_requires_admin(client):
    _, msg_id = _failed_text(client, "q_retry_noauth", "nope")
    r = client.post(f"/api/admin/messages/{msg_id}/retry")
    assert r.status_code == 401
