"""Photo strips keep bounded pixels, approval gates and durable queue semantics."""

from __future__ import annotations

import base64
import io
import queue
import sqlite3
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import app as app_module
import config
from auth import db as auth_db, session as sess
from features import image as image_feat, photo


@pytest.fixture
def client():
    return app_module.app.test_client()


def _login(client, name, status="allowed"):
    user = auth_db.create_pending_user(name, "photo-password")
    auth_db.set_status(user["id"], status)
    with client.session_transaction() as session:
        session[sess.SESSION_USER_KEY] = user["id"]
    return user


def _png(color="gray", size=(100, 120), mode="RGB"):
    image = Image.new(mode, size, color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _form(count=2, **values):
    return {"photos": [(io.BytesIO(_png()), f"frame-{i}.png") for i in range(count)],
            "caption": "small moments", "treatment": "soft", **values}


def _decoded(url):
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))


@pytest.mark.parametrize("width", [384, 576])
def test_four_frames_fit_receipt_and_keep_white_margins(monkeypatch, width):
    """Four frames plus a maximum caption fit the image budget on either paper width."""
    monkeypatch.setattr(config, "PRINTER_PIXEL_WIDTH", width)
    strip = photo.render_strip([_png("black")] * 4, "ink", "caption " * 20)
    assert strip.mode == "1"
    assert strip.width == width
    assert strip.height <= image_feat.MAX_OUTPUT_HEIGHT
    # The left edge beside the frames stays white, even for fully black photos.
    margin = width // 24
    side = width - margin * 2
    first_y = photo.render_feat.render_markup("> PHOTO BOOTH").height + max(8, width // 36)
    assert strip.convert("L").getpixel((margin - 1, first_y + side // 2)) == 255
    assert strip.getpixel((margin, first_y + side // 2)) == 0


def test_thermal_treatments_are_distinct():
    """Soft grain, high contrast and solid ink produce distinct 1-bit pixels."""
    raw = io.BytesIO()
    Image.linear_gradient("L").save(raw, format="PNG")
    results = [photo.render_strip([raw.getvalue()], mode) for mode in photo.TREATMENTS]
    assert all(result.mode == "1" for result in results)
    assert len({result.tobytes() for result in results}) == 3


def test_photo_rotates_exif_before_square_crop_and_flattens_alpha():
    """EXIF rotation determines the crop direction; transparent regions print white."""
    source = Image.new("RGB", (60, 100), "white")
    ImageDraw.Draw(source).rectangle((0, 0, 29, 99), fill="black")
    exif = Image.Exif()
    exif[274] = 6
    raw = io.BytesIO()
    source.save(raw, format="JPEG", exif=exif)
    frame = photo._frame(raw.getvalue(), "ink", 100)
    assert frame.getpixel((50, 10)) == 0
    assert frame.getpixel((50, 90)) == 255
    alpha = photo._frame(_png((0, 0, 0, 0), mode="RGBA"), "ink", 100)
    assert alpha.convert("L").getextrema() == (255, 255)


@pytest.mark.parametrize("case", ["bytes", "pixels", "corrupt", "format", "animated"])
def test_photo_input_guards(case):
    """Reject oversized, malformed, unsupported and animated inputs before queueing."""
    if case == "bytes":
        raw = b"x" * (photo.MAX_FILE_BYTES + 1)
    elif case == "pixels":
        raw = _png(0, (8000, 4000), "1")
    elif case == "corrupt":
        raw = _png()[:70]
    else:
        buf = io.BytesIO()
        image = Image.new("RGB", (10, 10), "red")
        if case == "format":
            image.save(buf, format="GIF")
        else:
            image.save(buf, format="PNG", save_all=True,
                       append_images=[Image.new("RGB", (10, 10), "blue")])
        raw = buf.getvalue()
    with pytest.raises(ValueError):
        photo.render_strip([raw])


def test_photo_routes_require_an_approved_friend(client, monkeypatch):
    """Both photo routes reject guests, pending and blocked users before decoding uploads."""
    monkeypatch.setattr(photo, "render_strip", lambda *a, **kw: pytest.fail("decoded before auth"))
    for route in ("/api/photo/preview", "/api/print/photo"):
        assert client.post(route, data=_form()).status_code == 401
    for state in ("pending", "blocked"):
        _login(client, "photo_gate_" + state, state)
        for route in ("/api/photo/preview", "/api/print/photo"):
            assert client.post(route, data=_form()).status_code == 403


def test_photo_routes_reject_invalid_forms_without_history(client):
    """Preview and send share limits and return JSON input errors without spending slots."""
    user = _login(client, "photo_bad_form")
    for route in ("/api/photo/preview", "/api/print/photo"):
        for data in ({}, _form(5), _form(treatment="unknown"), _form(caption="x" * 161),
                     {"photos": (io.BytesIO(b"broken image"), "photo.png")},
                     {"saved_id": "bad-id"}, {"saved_id": "9" * 100}, _form(saved_id="1")):
            response = client.post(route, data=data)
            assert response.status_code == 400
            assert response.get_json()["kind"] == "input"
    assert auth_db.list_messages_for_user(user["id"]) == []
    assert app_module._inflight.get(user["id"], 0) == 0


def test_photo_upload_keeps_global_body_cap(client):
    """Oversized multipart requests retain the app's JSON 413 backstop."""
    _login(client, "photo_body_cap")
    response = client.post("/api/print/photo", data={
        "photos": (io.BytesIO(b"x" * (16 * 1024 * 1024)), "huge.png"),
    })
    assert response.status_code == 413
    assert response.get_json()["kind"] == "input"


def test_photo_preview_matches_saved_pixels_and_does_not_enqueue(client):
    """The server preview's strip is byte-for-pixel identical to the durable print raster."""
    user = _login(client, "photo_preview")
    preview = client.post("/api/photo/preview", data=_form(4, anonymous="true"))
    assert preview.status_code == 200
    assert auth_db.list_messages_for_user(user["id"]) == []
    segments = preview.get_json()["segments"]
    assert len(segments) == 3
    printed = client.post("/api/print/photo", data=_form(4, anonymous="true"))
    assert printed.get_json()["queued"] is True
    app_module._PRINT_QUEUE.join()
    history = client.get("/api/history").get_json()["messages"]
    row = history[0]
    assert row["kind"] == "photo" and row["anonymous"] is True
    assert row["status"] == "printed" and row["has_drawing"] is True
    saved = auth_db.get_message(row["id"])
    strip = Image.open(io.BytesIO(saved["drawing"]))
    assert strip.mode == "1"
    assert strip.convert("L").tobytes() == _decoded(segments[1]).convert("L").tobytes()
    assert app_module._inflight.get(user["id"], 0) == 0
    # A strip taller than 256 rows must use the printer's fragment helper.
    assert Path(config.DRY_RUN_PATH).read_bytes().count(b"\x1dv0") > 4


def test_photo_history_reuse_is_scoped_and_preserves_pixels(client):
    """A friend can reprint their saved strip; another friend and doodle ids are rejected."""
    user = _login(client, "photo_history_owner")
    client.post("/api/print/photo", data=_form())
    app_module._PRINT_QUEUE.join()
    original = auth_db.get_message(auth_db.list_messages_for_user(user["id"])[0]["id"])
    response = client.post("/api/print/photo", data={"saved_id": original["id"], "anonymous": "true"})
    assert response.get_json()["queued"] is True
    app_module._PRINT_QUEUE.join()
    copy = auth_db.get_message(auth_db.list_messages_for_user(user["id"])[0]["id"])
    assert copy["id"] != original["id"]
    assert copy["drawing"] == original["drawing"] and copy["body"] == original["body"]
    assert copy["anonymous"] is True
    doodle = auth_db.log_message(user["id"], "(doodle)", drawing=_png())
    assert client.post("/api/photo/preview", data={"saved_id": doodle}).status_code == 400
    _login(client, "photo_history_other")
    for route in ("/api/photo/preview", "/api/print/photo"):
        assert client.post(route, data={"saved_id": original["id"]}).status_code == 400


def test_photo_queue_caps_and_unwind(client, monkeypatch):
    """Photos share text/doodle caps and release the slot and history after enqueue failure."""
    user = _login(client, "photo_queue_limits")
    app_module._inflight[user["id"]] = app_module._PER_USER_QUEUE_CAP
    try:
        response = client.post("/api/print/photo", data=_form())
        assert response.status_code == 429
        assert response.get_json()["kind"] == "user_cap"
    finally:
        app_module._inflight.pop(user["id"], None)

    def full(_item):
        raise queue.Full

    with monkeypatch.context() as patch:
        patch.setattr(app_module._PRINT_QUEUE, "put_nowait", full)
        assert client.post("/api/print/photo", data=_form()).status_code == 503
    assert app_module._inflight.get(user["id"], 0) == 0
    assert auth_db.list_messages_for_user(user["id"]) == []

    def broken_db(*args, **kwargs):
        raise sqlite3.OperationalError("test photo write failure")

    monkeypatch.setattr(auth_db, "log_message", broken_db)
    assert client.post("/api/print/photo", data=_form()).status_code == 500
    assert app_module._inflight.get(user["id"], 0) == 0


def test_photo_restart_replay_and_owner_retry_preserve_printer_bytes(client):
    """Restart replay and owner retry need only the row and reproduce the entire receipt."""
    user = _login(client, "photo_durable")
    client.post("/api/print/photo", data=_form(1, anonymous="true"))
    app_module._PRINT_QUEUE.join()
    message = auth_db.list_messages_for_user(user["id"])[0]
    output = Path(config.DRY_RUN_PATH)
    original = output.read_bytes()
    auth_db.set_message_status(message["id"], "queued")
    app_module._replay_queued()
    app_module._PRINT_QUEUE.join()
    assert output.read_bytes() == original
    auth_db.set_message_status(message["id"], "failed")
    retry = client.post(f"/api/admin/messages/{message['id']}/retry", headers={
        "Authorization": f"Bearer {config.ADMIN_TOKEN}",
    })
    assert retry.status_code == 200
    assert output.read_bytes() == original
    assert auth_db.get_message(message["id"])["status"] == "printed"


def test_kind_migration_keeps_existing_text_and_doodles(tmp_path, monkeypatch):
    """Upgrading an existing SQLite file backfills kinds without losing old image bytes."""
    path = tmp_path / "pre-photo.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE messages (id INTEGER PRIMARY KEY, user_id INTEGER,
              body TEXT, drawing BLOB, printed_at TEXT);
            INSERT INTO messages VALUES (1, 1, 'old text', NULL, '2026-09-05');
            INSERT INTO messages VALUES (2, 1, '(doodle)', X'1234', '2026-09-05');
        """)
    monkeypatch.setattr(config, "DB_PATH", path)
    auth_db.init()
    auth_db.init()
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT kind, body, drawing FROM messages ORDER BY id").fetchall()
    assert rows == [("text", "old text", None), ("doodle", "(doodle)", b"\x12\x34")]
