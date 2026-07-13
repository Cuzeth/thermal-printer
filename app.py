"""Thermal printer master GUI — Flask backend.

Run with:  python3 app.py
Open:      http://127.0.0.1:5005
"""

from __future__ import annotations

import base64
import binascii
import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Callable

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

import config
from auth import auth_bp
from auth import db as auth_db
from auth.blueprint import validate_password
from auth.session import current_user, require_admin, require_allowed, require_owner
from auth.access import require_access
from features import codes as codes_feat
from features import hardware as hw_feat
from features import image as image_feat
from features import led as led_feat
from features import render as render_feat
from features import text as text_feat
from features import widgets
from printer import PrinterError, footer, open_printer, print_image as _print_image, reset_device, status as printer_status


app = Flask(__name__)
app.config.update(
    SECRET_KEY=config.SECRET_KEY,
    # Secure requires HTTPS — true on the Pi (Cloudflare terminates TLS
    # for both hostnames), false in local dev.
    # Gated by an explicit env var rather than FLASK_DEBUG because debug is
    # usually off in dev too, which would silently kill the session cookie.
    SESSION_COOKIE_SECURE=config.COOKIE_SECURE,
    SESSION_COOKIE_HTTPONLY=True,
    # Lax + POST-only state changes = no CSRF surface worth protecting.
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,  # 30 days
    # Backstop for the public routes: nobody legitimately sends more than
    # an image upload here, and the Pi has to buffer whatever arrives.
    # Werkzeug answers oversized bodies with 413 before route code runs.
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # 16MB
)
app.register_blueprint(auth_bp)
auth_db.init()


@app.after_request
def _security_headers(resp):
    # Cheap hardening — /m/* is on the public internet at print.cuzeth.com. DENY
    # framing (nothing here is meant to be embedded), stop MIME sniffing,
    # and keep referrers on-site.
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


@app.errorhandler(413)
def _too_large(e):
    return jsonify({"ok": False, "error": "request too large (max 16MB)",
                    "kind": "input"}), 413


@app.route("/")
@require_access
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


def _print_doodle(job: dict) -> None:
    """Header, the drawing, timestamp footer — one tear-off. Header and
    footer are rasterized like any markup; the doodle goes between them
    as its own transfer, same buffer-safe shape as _print_sections."""
    header = render_feat.render_markup(job["header"])
    footer_img = render_feat.render_markup(job["footer"])
    with open_printer() as p:
        _print_image(p, header)
        _print_image(p, job["image"])
        _print_image(p, footer_img)
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
    except HTTPException:
        # Let Flask's own error handlers answer (e.g. the JSON 413 for
        # body-too-large). Without this, the catch-all turns a werkzeug
        # abort into a misleading 500.
        raise
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e), "kind": "server"}), 500


# ---------- text composer ----------

@app.post("/api/preview")
@require_access
@require_owner
def preview():
    body = (request.get_json(silent=True) or {}).get("body", "")
    return jsonify({"ok": True, "preview": text_feat.preview(body), "width": config.RECEIPT_WIDTH})


@app.post("/api/preview/rich")
@require_access
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
@require_access
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
@require_access
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
@require_access
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
@require_access
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
@require_access
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
@require_access
@require_owner
def print_hn():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(widgets.hacker_news(count=int(data.get("count", 5))))
        return {}
    return _safe(run)


@app.post("/api/print/onthisday")
@require_access
@require_owner
def print_on_this_day():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(widgets.on_this_day(count=int(data.get("count", 4))))
        return {}
    return _safe(run)


@app.post("/api/print/calendar")
@require_access
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
@require_access
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
@require_access
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
@require_access
@require_owner
def print_advice():
    def run():
        _print_body(widgets.advice())
        return {}
    return _safe(run)


@app.post("/api/print/briefing")
@require_access
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
@require_access
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
@require_access
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
@require_access
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
@require_access
@require_owner
def print_ascii():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(widgets.ascii_art(data.get("name", "")))
        return {}
    return _safe(run)


@app.post("/api/print/now")
@require_access
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
@require_access
@require_owner
def qr_preview():
    def run():
        opts = _qr_opts(request.get_json(silent=True) or {})
        img = codes_feat.make_qr_image(opts)
        return {"data_url": image_feat.to_png_data_url(img),
                "width": img.width, "height": img.height}
    return _safe(run)


@app.post("/api/print/qr")
@require_access
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
@require_access
@require_owner
def barcode_preview():
    def run():
        opts = _barcode_opts(request.get_json(silent=True) or {})
        img = codes_feat.make_barcode_image(opts)
        return {"data_url": image_feat.to_png_data_url(img),
                "width": img.width, "height": img.height}
    return _safe(run)


@app.post("/api/print/barcode")
@require_access
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
@require_access
@require_owner
def barcode_types():
    return jsonify({"ok": True,
                    "types": list(codes_feat.BARCODE_TYPES.keys()),
                    "hri": codes_feat.HRI_POSITIONS})


# ---------- hardware controls ----------

@app.post("/api/hw/cash_drawer")
@require_access
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
@require_access
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
@require_access
@require_owner
def hw_feed():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.feed_lines(p, int(data.get("lines", 3)))
        return {}
    return _safe(run)


@app.post("/api/hw/cut")
@require_access
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
@require_access
@require_owner
def hw_reset():
    def run():
        with open_printer() as p:
            hw_feat.reset(p)
        return {}
    return _safe(run)


@app.post("/api/hw/self_test")
@require_access
@require_owner
def hw_self_test():
    def run():
        with open_printer() as p:
            hw_feat.self_test(p)
        return {}
    return _safe(run)


@app.post("/api/hw/density")
@require_access
@require_owner
def hw_density():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.set_density(p, int(data.get("level", 8)))
        return {}
    return _safe(run)


@app.post("/api/hw/codepage")
@require_access
@require_owner
def hw_codepage():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.set_code_page(p, int(data.get("n", 0)))
        return {}
    return _safe(run)


@app.get("/api/hw/codepages")
@require_access
@require_owner
def hw_codepages():
    return jsonify({
        "ok": True,
        "pages": [{"n": n, "label": label} for n, label in hw_feat.CODE_PAGES.items()],
    })


@app.post("/api/hw/status")
@require_access
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
@require_access
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
@require_access
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
@require_access
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
@require_access
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
@require_access
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

# Canvas PNGs are tiny (white bg + strokes compress well); 2MB of decoded
# PNG is already far beyond any honest doodle. Guards the b64 decode, the
# 16MB global body cap guards the transport.
_MAX_DOODLE_BYTES = 2 * 1024 * 1024
_DOODLE_PREFIX = "data:image/png;base64,"

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
# (user_id, message_id, job_dict) — job is {"kind": "text", "body": <markup>}
# or {"kind": "doodle", "image": <1-bit PIL image>, "header": <markup>,
# "footer": <markup>}.
_PRINT_QUEUE: "queue.Queue[tuple[int, int, dict]]" = queue.Queue(maxsize=_PRINT_QUEUE_MAX)

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
        user_id, msg_id, job = _PRINT_QUEUE.get()
        status = "printed"
        try:
            if job["kind"] == "doodle":
                _print_doodle(job)
            else:
                _print_body(job["body"])
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


# ---------- scheduled briefing (opt-in) ----------

def _parse_schedule(value: str) -> dt_time | None:
    """Parse BRIEFING_SCHEDULE ("HH:MM", 24h) or None when unset.
    Raises ValueError on garbage so a typo'd .env fails at boot, loudly,
    instead of never printing and never saying why."""
    if not value:
        return None
    try:
        # Exactly one colon, both halves integers, hour/minute in range —
        # dt_time() enforces the ranges, the unpack enforces the shape.
        hh, mm = value.split(":")
        return dt_time(int(hh), int(mm))
    except ValueError:
        raise ValueError(
            f"BRIEFING_SCHEDULE must be a 24h time like 07:30, got {value!r}"
        ) from None


def _seconds_until(target: dt_time, now: datetime) -> float:
    """Seconds from `now` to the next occurrence of `target` (today if
    still ahead, else tomorrow). Pure function so tests don't sleep."""
    candidate = datetime.combine(now.date(), target)
    if candidate <= now:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()


def _briefing_scheduler(target: dt_time) -> None:
    while True:
        # Re-check the clock at most every 60s rather than one long
        # sleep — robust to NTP jumps and suspend/resume on the Pi.
        remaining = _seconds_until(target, datetime.now())
        if remaining > 60:
            time.sleep(60)
            continue
        time.sleep(remaining)
        try:
            _print_sections(widgets.morning_briefing_sections())
        except Exception:
            # Never let a flaky widget or an offline printer kill the
            # scheduler — tomorrow is another morning.
            traceback.print_exc()
        # Skip past the target minute so we fire once per day.
        time.sleep(61)


_briefing_time = _parse_schedule(config.BRIEFING_SCHEDULE)
if _briefing_time is not None:
    threading.Thread(target=_briefing_scheduler, args=(_briefing_time,),
                     name="briefing-scheduler", daemon=True).start()


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


def _enqueue_friend_print(user: dict, history_body: str, job: dict):
    """Shared bookkeeping for every friend print kind: per-user cap,
    optimistic history row, queue insert, and the crash-safe unwind.
    Returns a Flask response."""
    with _inflight_lock:
        if _inflight.get(user["id"], 0) >= _PER_USER_QUEUE_CAP:
            return jsonify({
                "ok": False,
                "error": f"you already have {_PER_USER_QUEUE_CAP} prints queued — "
                         "let them finish first",
                "kind": "user_cap",
            }), 429
        _inflight[user["id"]] = _inflight.get(user["id"], 0) + 1

    msg_id = None
    try:
        # Log to history at enqueue time so the friend sees their message
        # immediately, even before the worker actually pulls it. The row
        # starts 'queued'; the worker flips it to 'printed' or 'failed' so
        # the friend can see whether it actually hit paper.
        msg_id = auth_db.log_message(user["id"], history_body, status="queued")

        # qsize() before put = jobs the printer must finish first.
        # Approximate (the worker may have started one but not yet
        # decremented), but close enough for a UI hint.
        ahead = _PRINT_QUEUE.qsize()
        _PRINT_QUEUE.put_nowait((user["id"], msg_id, job))
    except queue.Full:
        _dec_inflight(user["id"])
        auth_db.delete_message(msg_id)  # never entered the queue
        return jsonify({
            "ok": False,
            "error": "the print queue is full — try again in a minute",
            "kind": "queue_full",
        }), 503
    except Exception:
        # Bookkeeping failed (most likely a SQLite hiccup). Undo the
        # in-flight increment so the friend isn't locked out of the cap
        # until the next restart, and answer in the JSON shape the page
        # expects instead of Flask's HTML 500.
        _dec_inflight(user["id"])
        if msg_id is not None:
            try:
                auth_db.delete_message(msg_id)
            except Exception:
                pass
        traceback.print_exc()
        return jsonify({"ok": False, "error": "internal error",
                        "kind": "server"}), 500

    return jsonify({"ok": True, "queued": True, "ahead": ahead})


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
    return _enqueue_friend_print(user, body, {"kind": "text", "body": formatted})


@app.post("/api/m/print/doodle")
@require_allowed
def friend_print_doodle():
    user = current_user()
    data = request.get_json(silent=True) or {}
    raw = data.get("image") or ""
    if not isinstance(raw, str) or not raw.startswith(_DOODLE_PREFIX):
        return jsonify({"ok": False, "error": "no drawing attached",
                        "kind": "input"}), 400
    try:
        png = base64.b64decode(raw[len(_DOODLE_PREFIX):], validate=True)
    except binascii.Error:
        return jsonify({"ok": False, "error": "bad image data",
                        "kind": "input"}), 400
    if len(png) > _MAX_DOODLE_BYTES:
        return jsonify({"ok": False, "error": "drawing too large",
                        "kind": "input"}), 400
    try:
        img = image_feat.process(
            png, image_feat.ProcessOptions(mode="threshold", threshold=160))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e), "kind": "input"}), 400
    # An untouched canvas thresholds to pure white — nothing to print.
    if img.convert("L").getextrema()[0] == 255:
        return jsonify({"ok": False, "error": "draw something first",
                        "kind": "input"}), 400
    img = image_feat.pad_to_printer_width(img)
    header, footer_markup = widgets.friend_frame(
        user["username"],
        style=user.get("name_style") or "plain",
        anonymous=bool(data.get("anonymous", False)),
    )
    return _enqueue_friend_print(user, "(doodle)", {
        "kind": "doodle", "image": img,
        "header": header, "footer": footer_markup,
    })


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


@app.get("/api/m/printer")
@require_allowed
def friend_printer_status():
    """Last-known printer reachability for the soft banner on /m/.

    Deliberately coarse: True until a real USB open fails, False until a
    print completes. Queueing is unaffected — the banner only sets
    expectations."""
    return jsonify({"ok": True, "printer": printer_status()})


# ---------- admin (Bearer-token gated) ----------

@app.get("/api/admin/users")
@require_access
@require_admin
def admin_list_users():
    status = request.args.get("status")
    try:
        users = auth_db.list_users(status=status)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "users": users})


@app.post("/api/admin/users/<int:user_id>/approve")
@require_access
@require_admin
def admin_approve_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "no such user"}), 404
    auth_db.set_status(user_id, "allowed")
    return jsonify({"ok": True})


@app.post("/api/admin/users/<int:user_id>/revoke")
@require_access
@require_admin
def admin_revoke_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "no such user"}), 404
    auth_db.set_status(user_id, "blocked")
    return jsonify({"ok": True})


@app.post("/api/admin/users/<int:user_id>/delete")
@require_access
@require_admin
def admin_delete_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "no such user"}), 404
    auth_db.delete_user(user_id)
    return jsonify({"ok": True})


@app.post("/api/admin/users/<int:user_id>/password")
@require_access
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
@require_access
@require_admin
def admin_list_messages():
    try:
        limit = max(1, min(200, int(request.args.get("limit", 20))))
    except (TypeError, ValueError):
        limit = 20
    return jsonify({"ok": True, "messages": auth_db.list_messages(limit=limit)})


@app.post("/api/admin/printer/reset")
@require_access
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
    if config.BRIEFING_SCHEDULE:
        print(f"Scheduled briefing: daily at {config.BRIEFING_SCHEDULE}")


_print_banner()


if __name__ == "__main__":
    # Dev server. Prod runs under gunicorn (see deploy/thermal-printer.service).
    # FLASK_DEBUG=1 opts in to the Werkzeug reloader/debugger; never set it on
    # the Pi — /m/* is public at print.cuzeth.com and the debugger = RCE.
    app.run(host=config.HOST, port=config.PORT, debug=os.environ.get("FLASK_DEBUG") == "1")
