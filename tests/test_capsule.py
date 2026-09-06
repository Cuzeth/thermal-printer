"""Capsules are durable, bounded, cancellable jobs with explicit UTC delivery."""

from __future__ import annotations

import base64
import io
import queue
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from PIL import Image, ImageDraw

import app as app_module
import config
from auth import db as auth_db, session as sess
from features import delivery
from printer import PrinterError


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=2)


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """No timer races or shared rows while testing clocks and database restarts."""
    real_queue = app_module._PRINT_QUEUE
    real_queue.join()
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "capsules.db")
    monkeypatch.setattr(app_module, "_inflight", {})
    monkeypatch.setattr(app_module, "_quiet_hours", None)
    monkeypatch.setattr(delivery, "utc_now", lambda: NOW)
    auth_db.init()
    yield app_module.app.test_client()
    real_queue.join()


def login(client, name="capsule_friend", status="allowed"):
    user = auth_db.create_pending_user(name, "capsule-password")
    auth_db.set_status(user["id"], status)
    with client.session_transaction() as session:
        session[sess.SESSION_USER_KEY] = user["id"]
    return user


def save(user, body="open later", when=LATER, **options):
    return auth_db.schedule_message(user["id"], body, delivery.stamp(when),
                                    delivery.stamp(when), **options)


def png():
    img = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(img).rectangle((20, 20, 80, 80), fill="black")
    data = io.BytesIO()
    img.save(data, "PNG")
    return data.getvalue()


def test_input_offsets_identify_same_instant():
    """Browser-local offsets and Z normalize to identical fixed-width UTC dates."""
    utc = delivery.parse_requested("2026-09-05T14:00:00Z", NOW)
    phoenix = delivery.parse_requested("2026-09-05T07:00:00-07:00", NOW)
    assert utc == phoenix == LATER
    assert delivery.stamp(utc) == "2026-09-05T14:00:00.000000Z"
    assert delivery.parse_requested(delivery.stamp(NOW + timedelta(days=365)), NOW)
    assert delivery.parse_requested(None, NOW) is None


@pytest.mark.parametrize("value", [True, 4, {}, [], "tomorrow", "2026-09-05",
    "2026-09-05T14:00", "2026-09-05T14:00:00+25:00", "2026-09-05T14:00:00+01:99", "2026-02-30T14:00Z",
    "2026-09-05T12:00Z", "2026-09-04T14:00Z", "2027-09-06T14:00Z"])
def test_reject_invalid_naive_past_and_unbounded_dates(value):
    """The server never guesses a timezone or silently accepts an invalid schedule."""
    with pytest.raises(ValueError):
        delivery.parse_requested(value, NOW)


@pytest.mark.parametrize("start,end,zone", [("22:00", "", "America/Phoenix"),
    ("22:00", "22:00", "America/Phoenix"), ("25:00", "07:00", "UTC"),
    ("22:00", "07:00", "Moon/Base"), ("7:00", "08:00", "UTC")])
def test_quiet_configuration_fails_loudly(start, end, zone):
    with pytest.raises(ValueError, match="FRIEND_QUIET"):
        delivery.parse_quiet(start, end, zone)


def test_quiet_hours_off_and_phoenix_boundaries():
    """Overnight start is inclusive and morning end exclusive in owner-local time."""
    assert delivery.parse_quiet("", "", "America/Phoenix") is None
    quiet = delivery.parse_quiet("22:00", "07:00", "America/Phoenix")
    before = datetime(2026, 9, 6, 4, 59, 59, tzinfo=timezone.utc)
    start = before + timedelta(seconds=1)
    end = datetime(2026, 9, 6, 14, tzinfo=timezone.utc)
    assert quiet.release(before) == before
    assert quiet.release(start) == end
    assert quiet.release(end - timedelta(seconds=1)) == end
    assert quiet.release(end) == end


def test_daytime_and_dst_quiet_boundaries():
    """Missing local end times skip forward; repeated minutes follow wall-clock rules."""
    day = delivery.parse_quiet("12:00", "13:30", "UTC")
    assert day.release(NOW) == NOW + timedelta(hours=1, minutes=30)
    spring = delivery.parse_quiet("22:00", "02:30", "America/New_York")
    assert spring.release(datetime(2026, 3, 8, 6, tzinfo=timezone.utc)) == datetime(
        2026, 3, 8, 7, tzinfo=timezone.utc)  # 03:00 local, 02:30 never happens.
    autumn = delivery.parse_quiet("22:00", "01:30", "America/New_York")
    assert autumn.release(datetime(2026, 11, 1, 5, tzinfo=timezone.utc)) == datetime(
        2026, 11, 1, 5, 30, tzinfo=timezone.utc)  # first 01:30
    assert autumn.release(datetime(2026, 11, 1, 6, tzinfo=timezone.utc)) == datetime(
        2026, 11, 1, 6, 30, tzinfo=timezone.utc)  # repeated 01:00 is quiet again.


@pytest.mark.parametrize("kind", ["text", "doodle", "photo"])
def test_all_content_kinds_stay_durable_then_print(isolated, monkeypatch, kind):
    """Text, drawings and photo pixels take the same durable path and keep anonymity."""
    user = login(isolated)
    when = delivery.stamp(LATER)
    if kind == "text":
        result = isolated.post("/api/print", json={"body": "surprise", "anonymous": True,
                                                  "deliver_at": when})
    elif kind == "doodle":
        result = isolated.post("/api/print/doodle", json={"anonymous": True, "deliver_at": when,
            "image": "data:image/png;base64," + base64.b64encode(png()).decode()})
    else:
        result = isolated.post("/api/print/photo", data={"anonymous": "true", "deliver_at": when,
            "caption": "future us", "photos": (io.BytesIO(png()), "photo.png")})
    assert result.status_code == 200
    assert result.json["scheduled"] and result.json["deliver_at"] == when
    msg = auth_db.get_message(result.json["id"])
    assert msg["status"] == "scheduled" and msg["kind"] == kind and msg["anonymous"]
    assert bool(msg["drawing"]) == (kind != "text")
    assert app_module._inflight.get(user["id"], 0) == 0
    assert app_module._dispatch_due(NOW) == 0
    # Reopening/migrating the same DB preserves payload and scheduled state.
    auth_db.init()
    assert auth_db.get_message(msg["id"])["drawing"] == msg["drawing"]
    monkeypatch.setattr(delivery, "utc_now", lambda: LATER)
    assert app_module._dispatch_due(LATER) == 1
    app_module._PRINT_QUEUE.join()
    assert auth_db.get_message(msg["id"])["status"] == "printed"
    assert app_module._inflight.get(user["id"], 0) == 0
    assert app_module._dispatch_due(LATER) == 0


def test_saved_photo_capsule_reuses_pixels_and_scopes_owner(isolated):
    user = login(isolated)
    original = auth_db.log_message(user["id"], "saved caption", drawing=png(), kind="photo")
    result = isolated.post("/api/print/photo", data={"saved_id": original,
        "deliver_at": delivery.stamp(LATER), "anonymous": "true"})
    assert result.status_code == 200
    capsule = auth_db.get_message(result.json["id"])
    assert capsule["drawing"] == png() and capsule["body"] == "saved caption"
    login(isolated, "capsule_other")
    hidden = isolated.post("/api/print/photo", data={"saved_id": original,
        "deliver_at": delivery.stamp(LATER)})
    assert hidden.status_code == 400 and hidden.json["kind"] == "input"


def test_invalid_route_schedule_is_json_and_never_queued(isolated):
    user = login(isolated)
    result = isolated.post("/api/print", json={"body": "oops", "deliver_at": "2026-09-05T14:00"})
    assert result.status_code == 400 and result.json["kind"] == "input"
    assert auth_db.list_messages_for_user(user["id"]) == []
    assert app_module._inflight == {}


def test_existing_database_migrates_without_losing_rows(isolated):
    """An old Pi schema receives nullable schedule columns, idempotently."""
    user = login(isolated)
    with auth_db.db() as conn:
        conn.execute("DROP INDEX idx_msgs_delivery")
        conn.execute("ALTER TABLE messages DROP COLUMN deliver_at")
        conn.execute("ALTER TABLE messages DROP COLUMN requested_for")
        conn.execute("INSERT INTO messages (user_id, body) VALUES (?, 'old receipt')", (user["id"],))
    auth_db.init()
    auth_db.init()
    row = auth_db.list_messages_for_user(user["id"])[0]
    assert row["body"] == "old receipt" and row["status"] == "printed"
    assert row["deliver_at"] is None and row["requested_for"] is None


def test_capsule_caps_include_claimed_jobs_and_cancel_releases_slot(isolated, monkeypatch):
    """Scheduling has durable limits independent of immediate in-flight slots."""
    user = login(isolated)
    ids = [save(user) for _ in range(delivery.PER_USER_CAP)]
    assert auth_db.claim_scheduled(ids[0], delivery.stamp(LATER))
    result = isolated.post("/api/print", json={"body": "eleventh", "deliver_at": delivery.stamp(LATER)})
    assert result.status_code == 429 and result.json["kind"] == "capsule_cap"
    assert auth_db.cancel_scheduled(ids[1], user["id"])
    assert save(user)
    # Normal sends still use their own three-slot limit.
    assert isolated.post("/api/print", json={"body": "now"}).status_code == 200
    app_module._PRINT_QUEUE.join()
    monkeypatch.setattr(delivery, "TOTAL_CAP", delivery.PER_USER_CAP)
    other = login(isolated, "capsule_global")
    with pytest.raises(auth_db.CapsuleLimit, match="storage full"):
        save(other)


def test_concurrent_schedules_share_durable_cap(isolated, monkeypatch):
    user = login(isolated)
    monkeypatch.setattr(delivery, "PER_USER_CAP", 1)
    gate = Barrier(2)
    def attempt():
        gate.wait()
        try:
            save(user)
            return "saved"
        except auth_db.CapsuleLimit:
            return "full"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["full", "saved"]


def test_due_queue_pressure_and_per_friend_cap_retry(isolated, monkeypatch):
    """Due capsules remain waiting under pressure, then dispatch once without leaks."""
    user = login(isolated)
    mid = save(user, when=NOW)
    fake = queue.Queue(maxsize=1)
    fake.put_nowait((999, 999))
    monkeypatch.setattr(app_module, "_PRINT_QUEUE", fake)
    assert app_module._dispatch_due(NOW) == 0
    assert auth_db.get_message(mid)["status"] == "scheduled"
    assert app_module._inflight == {}
    fake.get_nowait(); fake.task_done()
    app_module._inflight[user["id"]] = app_module._PER_USER_QUEUE_CAP
    assert app_module._dispatch_due(NOW) == 0
    app_module._inflight.clear()
    assert app_module._dispatch_due(NOW) == 1
    assert app_module._dispatch_due(NOW) == 0
    assert fake.get_nowait() == (user["id"], mid)
    fake.task_done()
    assert app_module._inflight[user["id"]] == 1
    app_module._dec_inflight(user["id"])


def test_enqueue_exception_returns_due_job_to_waiting(isolated, monkeypatch):
    user = login(isolated)
    mid = save(user, when=NOW)
    def broken(item):
        raise RuntimeError("queue unavailable")
    monkeypatch.setattr(app_module._PRINT_QUEUE, "put_nowait", broken)
    with pytest.raises(RuntimeError):
        app_module._dispatch_due(NOW)
    assert auth_db.get_message(mid)["status"] == "scheduled"
    assert app_module._inflight == {}


def test_replay_catches_up_overdue_capsules_with_queue_caps(isolated, monkeypatch):
    user = login(isolated)
    future = save(user)
    old = save(user, when=NOW - timedelta(days=3))
    claimed = save(user, when=NOW - timedelta(days=2))
    auth_db.claim_scheduled(claimed, delivery.stamp(NOW))
    seen = []
    monkeypatch.setattr(app_module, "_print_friend_message", lambda msg: seen.append(msg["id"]))
    auth_db.init()
    assert app_module._replay_queued() == 0  # Capsules return through due caps.
    assert auth_db.get_message(claimed)["status"] == "scheduled"
    assert app_module._dispatch_due(NOW) == 2
    app_module._PRINT_QUEUE.join()
    assert seen == [old, claimed]
    assert auth_db.get_message(future)["status"] == "scheduled"


def test_cancel_is_scoped_and_only_before_claim(isolated):
    user = login(isolated)
    mid = save(user)
    login(isolated, "capsule_cannot_cancel")
    hidden = isolated.post(f"/api/history/{mid}/cancel")
    missing = isolated.post("/api/history/999999/cancel")
    assert hidden.status_code == missing.status_code == 409
    assert hidden.json == missing.json
    with isolated.session_transaction() as session:
        session[sess.SESSION_USER_KEY] = user["id"]
    assert isolated.post(f"/api/history/{mid}/cancel").json["cancelled"]
    assert not auth_db.claim_scheduled(mid, delivery.stamp(LATER))
    claimed = save(user)
    assert auth_db.claim_scheduled(claimed, delivery.stamp(LATER))
    assert isolated.post(f"/api/history/{claimed}/cancel").status_code == 409


def test_cancel_and_claim_race_has_exactly_one_winner(isolated):
    user = login(isolated)
    mid = save(user, when=NOW)
    gate = Barrier(2)
    def cancel():
        gate.wait()
        return auth_db.cancel_scheduled(mid, user["id"])
    def claim():
        gate.wait()
        return auth_db.claim_scheduled(mid, delivery.stamp(NOW))
    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = pool.submit(cancel), pool.submit(claim)
        assert [left.result(), right.result()].count(True) == 1
    assert auth_db.get_message(mid)["status"] in ("queued", "cancelled")


@pytest.mark.parametrize("status", [None, "pending", "blocked"])
def test_cancel_requires_allowed_friend(isolated, status):
    if status:
        login(isolated, status=status)
    result = isolated.post("/api/history/1/cancel")
    assert result.status_code == (403 if status else 401)


def test_blocked_and_deleted_senders_never_dispatch(isolated, monkeypatch):
    user = login(isolated)
    mid = save(user, when=NOW)
    auth_db.set_status(user["id"], "blocked")
    assert auth_db.get_message(mid)["status"] == "cancelled"
    auth_db.set_status(user["id"], "allowed")
    assert app_module._dispatch_due(NOW) == 0
    other = login(isolated, "capsule_deleted")
    deleted = save(other, when=NOW)
    auth_db.delete_user(other["id"])
    assert auth_db.get_message(deleted) is None
    assert app_module._dispatch_due(NOW) == 0


def test_worker_rechecks_revocation_after_claim(isolated, monkeypatch):
    user = login(isolated)
    mid = save(user, when=NOW)
    assert auth_db.claim_scheduled(mid, delivery.stamp(NOW))
    # Simulate a stale queued payload after the account status changed.
    with auth_db.db() as conn:
        conn.execute("UPDATE users SET status = 'blocked' WHERE id = ?", (user["id"],))
    seen = []
    monkeypatch.setattr(app_module, "_print_friend_message", lambda msg: seen.append(msg))
    app_module._inflight[user["id"]] = 1
    app_module._PRINT_QUEUE.put((user["id"], mid))
    app_module._PRINT_QUEUE.join()
    assert not seen and auth_db.get_message(mid)["status"] == "cancelled"
    assert not app_module._inflight


def test_quiet_hours_hold_now_and_explicit_capsule(isolated, monkeypatch):
    user = login(isolated)
    quiet = delivery.parse_quiet("22:00", "08:00", "America/Phoenix")
    monkeypatch.setattr(app_module, "_quiet_hours", quiet)
    for options in ({}, {"deliver_at": delivery.stamp(LATER)}):
        response = isolated.post("/api/print", json={"body": "sleep in", **options})
        assert response.status_code == 200 and response.json["quiet_held"]
        assert response.json["deliver_at"] == delivery.stamp(NOW + timedelta(hours=3))
    assert len(auth_db.list_messages_for_user(user["id"])) == 2
    assert app_module._inflight == {}
    assert app_module._dispatch_due(LATER) == 0
    assert "America/Phoenix" in isolated.get("/").text


@pytest.mark.parametrize("scheduled", [False, True])
def test_worker_quiet_check_holds_jobs_queued_before_bed(isolated, monkeypatch, scheduled):
    """Immediate and scheduled jobs, including replay, recheck quiet hours at execution."""
    user = login(isolated)
    mid = save(user, when=NOW) if scheduled else auth_db.log_message(user["id"], "bedtime", status="queued")
    if scheduled:
        assert auth_db.claim_scheduled(mid, delivery.stamp(NOW))
    quiet = delivery.parse_quiet("22:00", "08:00", "America/Phoenix")
    monkeypatch.setattr(app_module, "_quiet_hours", quiet)
    seen = []
    monkeypatch.setattr(app_module, "_print_friend_message", lambda msg: seen.append(msg["id"]))
    app_module._inflight[user["id"]] = 1
    app_module._PRINT_QUEUE.put((user["id"], mid))
    app_module._PRINT_QUEUE.join()
    assert not seen and not app_module._inflight
    msg = auth_db.get_message(mid)
    assert msg["status"] == "scheduled"
    assert msg["deliver_at"] == delivery.stamp(NOW + timedelta(hours=3))
    monkeypatch.setattr(delivery, "utc_now", lambda: NOW + timedelta(hours=3))
    assert app_module._dispatch_due() == 1
    app_module._PRINT_QUEUE.join()
    assert seen == [mid]


def test_quiet_hours_do_not_change_owner_print_or_briefing(isolated, monkeypatch):
    monkeypatch.setattr(app_module, "_quiet_hours", delivery.parse_quiet("00:00", "23:59", "UTC"))
    seen = []
    monkeypatch.setattr(app_module, "_print_body", lambda body, **kw: seen.append(body))
    result = isolated.post("/api/admin/print/text", json={"body": "owner awake"},
        headers={"Authorization": f"Bearer {config.ADMIN_TOKEN}"})
    assert result.status_code == 200 and seen == ["owner awake"]
    # Briefing still calls the owner sections printer directly; no friend enqueue.
    monkeypatch.setattr(app_module.widgets, "morning_briefing_sections", lambda **kw: ["brief"])
    monkeypatch.setattr(app_module, "_print_sections", lambda sections: seen.extend(sections))
    result = isolated.post("/api/admin/print/briefing", json={},
        headers={"Authorization": f"Bearer {config.ADMIN_TOKEN}"})
    assert result.status_code == 200 and seen == ["owner awake", "brief"]


def test_due_printer_failure_is_visible_and_releases_caps(isolated, monkeypatch):
    user = login(isolated)
    mid = save(user, when=NOW)
    def offline(msg):
        raise PrinterError("offline")
    monkeypatch.setattr(app_module, "_print_friend_message", offline)
    assert app_module._dispatch_due(NOW) == 1
    app_module._PRINT_QUEUE.join()
    assert auth_db.get_message(mid)["status"] == "failed"
    assert not app_module._inflight
    assert app_module._dispatch_due(NOW) == 0


def test_scheduled_history_stays_visible_after_newer_prints(isolated):
    user = login(isolated)
    mid = save(user)
    for _ in range(55):
        auth_db.log_message(user["id"], "newer")
    result = isolated.get("/api/history").json
    assert len(result["messages"]) == 50
    assert result["messages"][0]["id"] == mid
    assert result["messages"][0]["requested_for"] == delivery.stamp(LATER)


def test_schedule_database_failure_keeps_slots_free(isolated, monkeypatch):
    login(isolated)
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("busy")
    monkeypatch.setattr(auth_db, "schedule_message", broken)
    result = isolated.post("/api/print", json={"body": "later", "deliver_at": delivery.stamp(LATER)})
    assert result.status_code == 500 and result.json["kind"] == "server"
    assert app_module._inflight == {}


def test_scheduler_survives_one_failed_tick(monkeypatch):
    """A temporary database error cannot silently kill future delivery ticks."""
    calls = []
    class StopLoop(BaseException):
        pass
    def tick():
        calls.append("tick")
        if len(calls) == 1:
            raise sqlite3.OperationalError("busy")
    def sleep(seconds):
        assert seconds == delivery.POLL_SECONDS
        if len(calls) == 2:
            raise StopLoop
    monkeypatch.setattr(app_module, "_dispatch_due", tick)
    monkeypatch.setattr(app_module.time, "sleep", sleep)
    with pytest.raises(StopLoop):
        app_module._capsule_scheduler()
    assert len(calls) == 2
