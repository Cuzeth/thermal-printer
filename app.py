"""Thermal printer master GUI — Flask backend.

Run with:  python3 app.py
Open:      http://127.0.0.1:5005
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
from typing import Any, Callable

from flask import Flask, jsonify, render_template, request

import config
from auth import auth_bp
from auth import db as auth_db
from auth.blueprint import validate_password
from auth.session import current_user, require_admin, require_allowed, require_owner
from auth.tailnet import require_tailnet
from features import codes as codes_feat
from features import hardware as hw_feat
from features import image as image_feat
from features import led as led_feat
from features import render as render_feat
from features import text as text_feat
from features import widgets
from printer import PrinterError, footer, open_printer, print_image as _print_image, reset_device


app = Flask(__name__)
app.config.update(
    SECRET_KEY=config.SECRET_KEY,
    # Secure requires HTTPS — true on the Pi (Funnel), false in local dev.
    # Gated by an explicit env var rather than FLASK_DEBUG because debug is
    # usually off in dev too, which would silently kill the session cookie.
    SESSION_COOKIE_SECURE=config.COOKIE_SECURE,
    SESSION_COOKIE_HTTPONLY=True,
    # Lax + POST-only state changes = no CSRF surface worth protecting.
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,  # 30 days
)
app.register_blueprint(auth_bp)
auth_db.init()


@app.after_request
def _security_headers(resp):
    # Cheap hardening — /m/* is on the public internet via Funnel. DENY
    # framing (nothing here is meant to be embedded), stop MIME sniffing,
    # and keep referrers on-site.
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


@app.route("/")
@require_tailnet
def index():
    return render_template(
        "index.html",
        width=config.RECEIPT_WIDTH,
        pixel_width=config.PRINTER_PIXEL_WIDTH,
        dry_run=config.DRY_RUN,
        admin_token=config.ADMIN_TOKEN,
        default_location=config.DEFAULT_LOCATION,
    )


@app.route("/m/")
@app.route("/m")
def friends_index():
    return render_template("friends.html")


# ---------- generic body-printer helper ----------
#
# Rasterization is CPU work (PIL on a Pi Zero); the USB lock serializes
# every print in the process. All helpers below render FIRST and only then
# open the printer, so a slow render never extends the critical section
# and delays queued friend prints.

def _render_rich_segments(body: str) -> list:
    """Rasterize each `!!!`-separated segment of a markup body."""
    segments = render_feat.split_cuts(body) or [body]
    return [render_feat.render_markup(seg) for seg in segments]


def _send_rich(p, images: list) -> None:
    """Send pre-rendered segment images, cutting between (not after) them."""
    for i, img in enumerate(images):
        _print_image(p, img)
        if i < len(images) - 1:
            p.cut()


def _print_body(body: str, cut: bool = True, rich: bool = True) -> None:
    if rich:
        images = _render_rich_segments(body)
        with open_printer() as p:
            _send_rich(p, images)
            if cut:
                footer(p)
    else:
        with open_printer() as p:
            text_feat.render(p, body)
            if cut:
                footer(p)


def _print_sections(sections: list[str]) -> None:
    """Print several markup bodies as back-to-back images on one scroll.

    Each section is rasterized and sent as its own GS v 0 transfer. This
    is the safe shape for long composite prints (e.g. morning briefing):
    one giant image can overrun the printer's raster buffer and come out
    as noise after the first chunk. Splitting keeps each USB transfer
    small while still feeling like a single continuous tear-off — no cut
    is issued between sections.
    """
    images = [
        render_feat.render_markup(seg)
        for seg in sections
        if (seg or "").strip()
    ]
    with open_printer() as p:
        for img in images:
            _print_image(p, img)
        footer(p)


def _safe(handler: Callable[[], Any]):
    """Common error-handling wrapper for POST routes."""
    try:
        result = handler()
        if isinstance(result, dict):
            return jsonify({"ok": True, **result})
        return jsonify({"ok": True})
    except PrinterError as e:
        return jsonify({"ok": False, "error": str(e), "kind": "printer"}), 503
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e), "kind": "input"}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e), "kind": "server"}), 500


# ---------- text composer ----------

@app.post("/api/preview")
@require_tailnet
@require_owner
def preview():
    body = (request.get_json(silent=True) or {}).get("body", "")
    return jsonify({"ok": True, "preview": text_feat.preview(body), "width": config.RECEIPT_WIDTH})


@app.post("/api/preview/rich")
@require_tailnet
@require_owner
def preview_rich():
    def run():
        body = (request.get_json(silent=True) or {}).get("body", "")
        segments = render_feat.split_cuts(body) or [body or ""]
        data_urls = []
        for seg in segments:
            img = render_feat.render_markup(seg)
            data_urls.append(image_feat.to_png_data_url(img))
        return {"segments": data_urls}
    return _safe(run)


@app.post("/api/print/text")
@require_tailnet
@require_owner
def print_text():
    def run():
        data = request.get_json(silent=True) or {}
        body = data.get("body", "").rstrip()
        if not body:
            raise ValueError("Nothing to print.")
        _print_body(
            body,
            cut=data.get("cut", True),
            rich=bool(data.get("rich", True)),
        )
        return {}
    return _safe(run)


# ---------- image ----------

def _image_opts_from_form() -> image_feat.ProcessOptions:
    """Preview and print must process identically — build opts in one place."""
    return image_feat.ProcessOptions(
        width=int(request.form.get("width", config.PRINTER_PIXEL_WIDTH)),
        contrast=float(request.form.get("contrast", 1.0)),
        brightness=float(request.form.get("brightness", 1.0)),
        invert=request.form.get("invert", "false").lower() == "true",
        mode=request.form.get("mode", "dither"),
        threshold=int(request.form.get("threshold", 128)),
    )


@app.post("/api/image/preview")
@require_tailnet
@require_owner
def image_preview():
    def run():
        if "file" not in request.files:
            raise ValueError("No file uploaded.")
        f = request.files["file"]
        img = image_feat.process(f.read(), _image_opts_from_form())
        return {"data_url": image_feat.to_png_data_url(img),
                "width": img.width, "height": img.height}
    return _safe(run)


@app.post("/api/print/image")
@require_tailnet
@require_owner
def print_image():
    def run():
        if "file" not in request.files:
            raise ValueError("No file uploaded.")
        f = request.files["file"]
        opts = _image_opts_from_form()
        caption = request.form.get("caption", "").strip()
        img = image_feat.process(f.read(), opts)
        img = image_feat.pad_to_printer_width(img)
        caption_imgs = _render_rich_segments(f"> {caption}") if caption else []
        with open_printer() as p:
            _print_image(p, img)
            if caption_imgs:
                p.text("\n")
                _send_rich(p, caption_imgs)
            footer(p)
        return {}
    return _safe(run)


# ---------- widget routes ----------

@app.post("/api/print/weather")
@require_tailnet
@require_owner
def print_weather():
    def run():
        data = request.get_json(silent=True) or {}
        loc = (data.get("location") or "").strip()
        if not loc:
            raise ValueError("location is required")
        days = int(data.get("days", 1))
        _print_body(widgets.weather(loc, days=days))
        return {}
    return _safe(run)


@app.post("/api/print/dice")
@require_tailnet
@require_owner
def print_dice():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(widgets.roll_dice(
            count=int(data.get("count", 2)),
            sides=int(data.get("sides", 6)),
            mode=str(data.get("mode", "standard")),
        ))
        return {}
    return _safe(run)


@app.post("/api/print/hn")
@require_tailnet
@require_owner
def print_hn():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(widgets.hacker_news(count=int(data.get("count", 5))))
        return {}
    return _safe(run)


@app.post("/api/print/onthisday")
@require_tailnet
@require_owner
def print_on_this_day():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(widgets.on_this_day(count=int(data.get("count", 4))))
        return {}
    return _safe(run)


@app.post("/api/print/calendar")
@require_tailnet
@require_owner
def print_calendar():
    def run():
        data = request.get_json(silent=True) or {}
        year = data.get("year")
        month = data.get("month")
        _print_body(widgets.calendar_month(
            year=int(year) if year else None,
            month=int(month) if month else None,
        ))
        return {}
    return _safe(run)


@app.post("/api/print/countdown")
@require_tailnet
@require_owner
def print_countdown():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(widgets.countdown(
            label=str(data.get("label", "")),
            target_iso=str(data.get("date", "")),
        ))
        return {}
    return _safe(run)


@app.post("/api/print/habits")
@require_tailnet
@require_owner
def print_habits():
    def run():
        data = request.get_json(silent=True) or {}
        items = data.get("habits") or []
        if not isinstance(items, list):
            raise ValueError("habits must be a list")
        _print_body(widgets.habit_tracker(
            habits=[str(h) for h in items],
            days=int(data.get("days", 7)),
        ))
        return {}
    return _safe(run)


@app.post("/api/print/advice")
@require_tailnet
@require_owner
def print_advice():
    def run():
        _print_body(widgets.advice())
        return {}
    return _safe(run)


@app.post("/api/print/briefing")
@require_tailnet
@require_owner
def print_briefing():
    def run():
        data = request.get_json(silent=True) or {}
        # Empty location falls through to config.DEFAULT_LOCATION in the
        # weather widget. Section-by-section path: one image per subsection
        # avoids the "garbled after the weather" issue that a single huge
        # raster caused on the real printer.
        loc = (data.get("location") or "").strip()
        _print_sections(widgets.morning_briefing_sections(location=loc))
        return {}
    return _safe(run)


@app.post("/api/print/todo")
@require_tailnet
@require_owner
def print_todo():
    def run():
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        items = data.get("items") or []
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        if not any((i or "").strip() for i in items):
            raise ValueError("At least one non-empty item is required.")
        _print_body(widgets.todo(title, [str(i) for i in items]))
        return {}
    return _safe(run)


@app.post("/api/print/receipt")
@require_tailnet
@require_owner
def print_receipt():
    def run():
        data = request.get_json(silent=True) or {}
        items = data.get("items") or []
        if not items:
            raise ValueError("At least one item is required.")
        _print_body(widgets.receipt(
            store=data.get("store", ""),
            items=items,
            tax_rate=float(data.get("tax_rate", 0.0) or 0.0),
            note=data.get("note", ""),
        ))
        return {}
    return _safe(run)


@app.post("/api/print/label")
@require_tailnet
@require_owner
def print_label():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(widgets.label(
            text=data.get("text", ""),
            big=bool(data.get("big", True)),
        ))
        return {}
    return _safe(run)


@app.post("/api/print/ascii")
@require_tailnet
@require_owner
def print_ascii():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(widgets.ascii_art(data.get("name", "")))
        return {}
    return _safe(run)


@app.post("/api/print/now")
@require_tailnet
@require_owner
def print_now():
    def run():
        _print_body(widgets.now_card())
        return {}
    return _safe(run)


# ---------- codes (QR / barcodes) ----------

def _qr_opts(data: dict) -> codes_feat.QROptions:
    return codes_feat.QROptions(
        data=data.get("data", ""),
        ec=data.get("ec", "M"),
        size=int(data.get("size", 8)),
        box_size=int(data.get("box_size", 10)),
    )


def _barcode_opts(data: dict) -> codes_feat.BarcodeOptions:
    return codes_feat.BarcodeOptions(
        kind=data.get("kind", "CODE128"),
        data=data.get("data", ""),
        width=int(data.get("width", 3)),
        height=int(data.get("height", 80)),
        hri=data.get("hri", "BELOW"),
        font=data.get("font", "A"),
    )


@app.post("/api/code/qr/preview")
@require_tailnet
@require_owner
def qr_preview():
    def run():
        opts = _qr_opts(request.get_json(silent=True) or {})
        img = codes_feat.make_qr_image(opts)
        return {"data_url": image_feat.to_png_data_url(img),
                "width": img.width, "height": img.height}
    return _safe(run)


@app.post("/api/print/qr")
@require_tailnet
@require_owner
def print_qr():
    def run():
        opts = _qr_opts(request.get_json(silent=True) or {})
        if not opts.data:
            raise ValueError("QR payload is empty.")
        with open_printer() as p:
            codes_feat.print_qr(p, opts)
            footer(p)
        return {}
    return _safe(run)


@app.post("/api/code/barcode/preview")
@require_tailnet
@require_owner
def barcode_preview():
    def run():
        opts = _barcode_opts(request.get_json(silent=True) or {})
        img = codes_feat.make_barcode_image(opts)
        return {"data_url": image_feat.to_png_data_url(img),
                "width": img.width, "height": img.height}
    return _safe(run)


@app.post("/api/print/barcode")
@require_tailnet
@require_owner
def print_barcode():
    def run():
        opts = _barcode_opts(request.get_json(silent=True) or {})
        if not opts.data:
            raise ValueError("Barcode payload is empty.")
        with open_printer() as p:
            codes_feat.print_barcode(p, opts)
            footer(p)
        return {}
    return _safe(run)


@app.get("/api/code/barcode/types")
@require_tailnet
@require_owner
def barcode_types():
    return jsonify({"ok": True,
                    "types": list(codes_feat.BARCODE_TYPES.keys()),
                    "hri": codes_feat.HRI_POSITIONS})


# ---------- hardware controls ----------

@app.post("/api/hw/cash_drawer")
@require_tailnet
@require_owner
def hw_cash_drawer():
    def run():
        data = request.get_json(silent=True) or {}
        pin = int(data.get("pin", 2))
        with open_printer() as p:
            hw_feat.cash_drawer(p, pin=pin)
        return {}
    return _safe(run)


@app.post("/api/hw/beep")
@require_tailnet
@require_owner
def hw_beep():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.beep(p,
                         count=int(data.get("count", 1)),
                         duration_units=int(data.get("duration_units", 3)))
        return {}
    return _safe(run)


@app.post("/api/hw/feed")
@require_tailnet
@require_owner
def hw_feed():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.feed_lines(p, int(data.get("lines", 3)))
        return {}
    return _safe(run)


@app.post("/api/hw/cut")
@require_tailnet
@require_owner
def hw_cut():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.cut_after_feed(
                p,
                lines=int(data.get("lines", 3)),
                partial=bool(data.get("partial", False)),
            )
        return {}
    return _safe(run)


@app.post("/api/hw/reset")
@require_tailnet
@require_owner
def hw_reset():
    def run():
        with open_printer() as p:
            hw_feat.reset(p)
        return {}
    return _safe(run)


@app.post("/api/hw/self_test")
@require_tailnet
@require_owner
def hw_self_test():
    def run():
        with open_printer() as p:
            hw_feat.self_test(p)
        return {}
    return _safe(run)


@app.post("/api/hw/density")
@require_tailnet
@require_owner
def hw_density():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.set_density(p, int(data.get("level", 8)))
        return {}
    return _safe(run)


@app.post("/api/hw/codepage")
@require_tailnet
@require_owner
def hw_codepage():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.set_code_page(p, int(data.get("n", 0)))
        return {}
    return _safe(run)


@app.get("/api/hw/codepages")
@require_tailnet
@require_owner
def hw_codepages():
    return jsonify({
        "ok": True,
        "pages": [{"n": n, "label": label} for n, label in hw_feat.CODE_PAGES.items()],
    })


@app.post("/api/hw/status")
@require_tailnet
@require_owner
def hw_status():
    def run():
        results = []
        with open_printer() as p:
            for mode, label in hw_feat.STATUS_MODES.items():
                val = hw_feat.query_status(p, mode=mode)
                entry = {"mode": mode, "label": label, "raw": val}
                if val is not None:
                    entry["flags"] = hw_feat.parse_status_byte(mode, val)
                results.append(entry)
        return {"statuses": results}
    return _safe(run)


_MAX_RAW_BYTES = 4096


@app.post("/api/hw/raw")
@require_tailnet
@require_owner
def hw_raw():
    def run():
        data = request.get_json(silent=True) or {}
        text = data.get("bytes", "")
        parsed = hw_feat.parse_raw_input(text)
        if not parsed:
            raise ValueError("nothing to send")
        if len(parsed) > _MAX_RAW_BYTES:
            raise ValueError(f"parsed {len(parsed)} bytes; max is {_MAX_RAW_BYTES}")
        with open_printer() as p:
            hw_feat.send_bytes(p, parsed)
        return {"sent": len(parsed)}
    return _safe(run)


@app.get("/api/hw/led/protocols")
@require_tailnet
@require_owner
def hw_led_protocols():
    return jsonify({
        "ok": True,
        "protocols": [
            {"key": p.key, "name": p.name, "note": p.note}
            for p in led_feat.PROTOCOLS.values()
        ],
    })


@app.post("/api/hw/led/preview")
@require_tailnet
@require_owner
def hw_led_preview():
    def run():
        data = request.get_json(silent=True) or {}
        bs = led_feat.build_bytes(
            data.get("protocol", "esc_c"),
            int(data.get("r", 0)),
            int(data.get("g", 0)),
            int(data.get("b", 0)),
        )
        return {"bytes": led_feat.hex_preview(bs), "length": len(bs)}
    return _safe(run)


@app.post("/api/hw/led")
@require_tailnet
@require_owner
def hw_led():
    def run():
        import time
        data = request.get_json(silent=True) or {}
        protocol = data.get("protocol", "esc_c")
        r = int(data.get("r", 0))
        g = int(data.get("g", 0))
        b = int(data.get("b", 0))
        blink = bool(data.get("blink", False))

        def set_color(pr, pg, pb):
            with open_printer() as p:
                return led_feat.send_color(p, protocol, pr, pg, pb)

        bs = set_color(r, g, b)
        if blink:
            # Release the USB lock between flashes so other requests aren't
            # blocked for 500ms while we sleep.
            time.sleep(0.25)
            set_color(0, 0, 0)
            time.sleep(0.25)
            set_color(r, g, b)
        return {"bytes": led_feat.hex_preview(bs)}
    return _safe(run)


@app.get("/api/hw/cheatsheet")
@require_tailnet
@require_owner
def hw_cheatsheet():
    return jsonify({
        "ok": True,
        "entries": [
            {"name": n, "hex": h, "desc": d}
            for n, h, d in hw_feat.CHEAT_SHEET
        ],
    })


# ---------- friend message endpoint ----------

_MAX_MSG_LEN = 800

# Friend-message print queue. POST /api/m/print enqueues and returns
# immediately; a single daemon worker drains in FIFO order so two friends
# hitting send at the same time both get an instant "queued" instead of
# one of them blocking on the USB lock for the duration of the other's
# print. Replaces the per-user 10s rate limit that used to reject bursts.
#
# Cap exists so a runaway client can't pin unbounded memory; on overflow
# we return 503 and the friend can retry once the printer catches up.
# Single-process only — load-bearing alongside `gunicorn --workers 1`.
_PRINT_QUEUE_MAX = 50
# (user_id, message_id, formatted_body)
_PRINT_QUEUE: "queue.Queue[tuple[int, int, str]]" = queue.Queue(maxsize=_PRINT_QUEUE_MAX)

# Per-user in-flight cap so one friend can't fill all 50 slots (paper DoS,
# and anonymous mode makes it socially cheap). Incremented at enqueue,
# decremented by the worker when the job finishes either way.
_PER_USER_QUEUE_CAP = 3
_inflight: dict[int, int] = {}
_inflight_lock = threading.Lock()


def _dec_inflight(user_id: int) -> None:
    with _inflight_lock:
        n = _inflight.get(user_id, 0) - 1
        if n > 0:
            _inflight[user_id] = n
        else:
            _inflight.pop(user_id, None)


def _print_worker() -> None:
    while True:
        user_id, msg_id, formatted = _PRINT_QUEUE.get()
        status = "printed"
        try:
            _print_body(formatted)
        except Exception as e:
            # Async failure — no HTTP response to attach this to. Log and
            # move on so one bad job doesn't wedge the queue for everyone.
            status = "failed"
            traceback.print_exc()
            print(
                f"[queue] print failed for user_id={user_id}: "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
                flush=True,
            )
        try:
            # Flip the history row so the friend can see whether it actually
            # hit paper. A DB hiccup here must not kill the worker thread.
            auth_db.set_message_status(msg_id, status)
        except Exception:
            traceback.print_exc()
        _dec_inflight(user_id)
        _PRINT_QUEUE.task_done()


threading.Thread(target=_print_worker, name="friend-print-worker", daemon=True).start()


@app.post("/api/m/preview")
@require_allowed
def friend_preview():
    """Render a WYSIWYG preview of what the message will print as.

    Runs the full friend_message() → render_markup() pipeline, so the
    preview includes the "from <username>" header and timestamp footer
    that will actually come out of the printer. No USB, no queue —
    pure in-process rendering.
    """
    def run():
        user = current_user()
        data = request.get_json(silent=True) or {}
        body = (data.get("body") or "").strip()
        if not body:
            return {"segments": []}
        if len(body) > _MAX_MSG_LEN:
            raise ValueError(f"message too long (max {_MAX_MSG_LEN} chars)")
        formatted = widgets.friend_message(
            user["username"],
            body,
            style=user.get("name_style") or "plain",
            anonymous=bool(data.get("anonymous", False)),
        )
        segments = render_feat.split_cuts(formatted) or [formatted]
        data_urls = [
            image_feat.to_png_data_url(render_feat.render_markup(seg))
            for seg in segments
        ]
        return {"segments": data_urls}
    return _safe(run)


@app.post("/api/m/print")
@require_allowed
def friend_print():
    user = current_user()
    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"ok": False, "error": "message is empty"}), 400
    if len(body) > _MAX_MSG_LEN:
        return jsonify({"ok": False, "error": f"message too long (max {_MAX_MSG_LEN} chars)"}), 400

    formatted = widgets.friend_message(
        user["username"],
        body,
        style=user.get("name_style") or "plain",
        anonymous=bool(data.get("anonymous", False)),
    )

    with _inflight_lock:
        if _inflight.get(user["id"], 0) >= _PER_USER_QUEUE_CAP:
            return jsonify({
                "ok": False,
                "error": f"you already have {_PER_USER_QUEUE_CAP} prints queued — "
                         "let them finish first",
                "kind": "user_cap",
            }), 429
        _inflight[user["id"]] = _inflight.get(user["id"], 0) + 1

    # Log to history at enqueue time so the friend sees their message
    # immediately, even before the worker actually pulls it. The row starts
    # 'queued'; the worker flips it to 'printed' or 'failed' so the friend
    # can see whether it actually hit paper.
    msg_id = auth_db.log_message(user["id"], body, status="queued")

    # qsize() before put = jobs the printer must finish first. Approximate
    # (the worker may have started one but not yet decremented), but close
    # enough for a UI hint.
    ahead = _PRINT_QUEUE.qsize()
    try:
        _PRINT_QUEUE.put_nowait((user["id"], msg_id, formatted))
    except queue.Full:
        _dec_inflight(user["id"])
        auth_db.delete_message(msg_id)  # never entered the queue
        return jsonify({
            "ok": False,
            "error": "the print queue is full — try again in a minute",
            "kind": "queue_full",
        }), 503

    return jsonify({"ok": True, "queued": True, "ahead": ahead})


@app.get("/api/m/history")
@require_allowed
def friend_history():
    """Return the signed-in friend's own print history, newest first.

    Scoped to `current_user()["id"]` — a friend can't peek at anyone else's
    messages by passing a user id in the query string. Limit mirrors the
    admin endpoint: clamped 1..200, default 50.
    """
    user = current_user()
    try:
        limit = max(1, min(200, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    return jsonify({
        "ok": True,
        "messages": auth_db.list_messages_for_user(user["id"], limit=limit),
    })


# ---------- admin (Bearer-token gated) ----------

@app.get("/api/admin/users")
@require_tailnet
@require_admin
def admin_list_users():
    status = request.args.get("status")
    try:
        users = auth_db.list_users(status=status)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "users": users})


@app.post("/api/admin/users/<int:user_id>/approve")
@require_tailnet
@require_admin
def admin_approve_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "no such user"}), 404
    auth_db.set_status(user_id, "allowed")
    return jsonify({"ok": True})


@app.post("/api/admin/users/<int:user_id>/revoke")
@require_tailnet
@require_admin
def admin_revoke_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "no such user"}), 404
    auth_db.set_status(user_id, "blocked")
    return jsonify({"ok": True})


@app.post("/api/admin/users/<int:user_id>/delete")
@require_tailnet
@require_admin
def admin_delete_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "no such user"}), 404
    auth_db.delete_user(user_id)
    return jsonify({"ok": True})


@app.post("/api/admin/users/<int:user_id>/password")
@require_tailnet
@require_admin
def admin_set_password(user_id: int):
    """Reset a friend's password. There's no self-service reset on /m/ —
    without this, a friend who forgot their password could only be deleted
    (losing their history and burning the username)."""
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "no such user"}), 404
    data = request.get_json(silent=True) or {}
    try:
        password = validate_password(data.get("password", ""))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    auth_db.set_password(user_id, password)
    return jsonify({"ok": True})


@app.get("/api/admin/messages")
@require_tailnet
@require_admin
def admin_list_messages():
    try:
        limit = max(1, min(200, int(request.args.get("limit", 20))))
    except (TypeError, ValueError):
        limit = 20
    return jsonify({"ok": True, "messages": auth_db.list_messages(limit=limit)})


@app.post("/api/admin/printer/reset")
@require_tailnet
@require_admin
def admin_reset_printer():
    """Issue a USB port reset to the printer — software unplug-replug."""
    if config.DRY_RUN:
        return jsonify({"ok": True, "reset": False, "dry_run": True})
    found = reset_device()
    if not found:
        return jsonify({"ok": False, "error": "printer not on USB bus"}), 503
    return jsonify({"ok": True, "reset": True})


# ---------- health ----------

@app.get("/api/ping")
def ping():
    return jsonify({"ok": True, "dry_run": config.DRY_RUN})


def _print_banner() -> None:
    print(f"Thermal Printer GUI -> http://{config.HOST}:{config.PORT}")
    if config.DRY_RUN:
        print(f"DRY RUN mode: bytes will be written to {config.DRY_RUN_PATH}")
    if not config.ADMIN_TOKEN_FROM_ENV:
        print(f"DEV ADMIN_TOKEN={config.ADMIN_TOKEN}  (set ADMIN_TOKEN in env to persist)")


_print_banner()


if __name__ == "__main__":
    # Dev server. Prod runs under gunicorn (see deploy/thermal-printer.service).
    # FLASK_DEBUG=1 opts in to the Werkzeug reloader/debugger; never set it on
    # the Pi — /m/* is public via Funnel and the debugger = RCE.
    app.run(host=config.HOST, port=config.PORT, debug=os.environ.get("FLASK_DEBUG") == "1")
