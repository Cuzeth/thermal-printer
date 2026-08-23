"""Thermal printer master GUI — Flask backend.

Run with:  python3 app.py
Open:      http://127.0.0.1:5005
"""

from __future__ import annotations

import base64
import binascii
import io
import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Callable

from PIL import Image
from flask import Flask, jsonify, redirect, render_template, request, send_from_directory
from werkzeug.exceptions import HTTPException

import config
from auth import auth_bp
from auth import db as auth_db
from auth.admin import admin_auth_bp
from auth.session import current_user, is_admin_request, require_admin, require_allowed
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
app.register_blueprint(admin_auth_bp)
auth_db.init()


@app.after_request
def _security_headers(resp):
    # Cheap hardening — the whole app is on the public internet at
    # print.cuzeth.com. DENY framing (nothing here is meant to be embedded),
    # stop MIME sniffing, and keep referrers on-site.
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


@app.errorhandler(413)
def _too_large(e):
    return jsonify({"ok": False, "error": "request exceeds 16 MB",
                    "kind": "input"}), 413


@app.route("/")
def friends_index():
    return render_template(
        "friends.html",
        width=config.RECEIPT_WIDTH,
        pixel_width=config.PRINTER_PIXEL_WIDTH,
    )


@app.route("/m/")
@app.route("/m")
def legacy_friends_redirect():
    # Friends' bookmarks predate the /m -> / move; don't 404 them.
    return redirect("/", code=301)


@app.route("/favicon.ico")
def favicon_ico():
    # Browsers probe this exact root path regardless of <link> tags.
    # Deliberately ungated, same as /static/*.
    return send_from_directory(app.static_folder, "favicon.ico")


@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def apple_touch_icon():
    # iOS probes these root paths too when someone shares or pins the page.
    return send_from_directory(app.static_folder, "apple-touch-icon.png")


@app.route("/admin")
def admin_index():
    # The page gate renders the login form instead of a JSON 401 — a
    # browser deserves a code prompt. Every /api/admin route underneath
    # still carries @require_admin itself.
    if not is_admin_request():
        return render_template("admin_login.html")
    return render_template(
        "index.html",
        width=config.RECEIPT_WIDTH,
        pixel_width=config.PRINTER_PIXEL_WIDTH,
        dry_run=config.DRY_RUN,
        default_location=config.DEFAULT_LOCATION,
    )


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
        return jsonify({"ok": False, "error": "server error", "kind": "server"}), 500


# ---------- text composer ----------

@app.post("/api/admin/preview")
@require_admin
def preview():
    body = (request.get_json(silent=True) or {}).get("body", "")
    return jsonify({"ok": True, "preview": text_feat.preview(body), "width": config.RECEIPT_WIDTH})


@app.post("/api/admin/preview/rich")
@require_admin
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


@app.post("/api/admin/print/text")
@require_admin
def print_text():
    def run():
        data = request.get_json(silent=True) or {}
        body = data.get("body", "").rstrip()
        if not body:
            raise ValueError("nothing to print")
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


@app.post("/api/admin/image/preview")
@require_admin
def image_preview():
    def run():
        if "file" not in request.files:
            raise ValueError("choose a file")
        f = request.files["file"]
        img = image_feat.process(f.read(), _image_opts_from_form())
        return {"data_url": image_feat.to_png_data_url(img),
                "width": img.width, "height": img.height}
    return _safe(run)


@app.post("/api/admin/print/image")
@require_admin
def print_image():
    def run():
        if "file" not in request.files:
            raise ValueError("choose a file")
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

def _widget_body(kind: str, data: dict) -> str:
    """Build a widget receipt body without touching USB."""
    if kind == "weather":
        loc = (data.get("location") or "").strip()
        if not loc:
            raise ValueError("enter a location")
        return widgets.weather(loc, days=int(data.get("days", 1)))
    if kind == "dice":
        return widgets.roll_dice(
            count=int(data.get("count", 2)),
            sides=int(data.get("sides", 6)),
            mode=str(data.get("mode", "standard")),
        )
    if kind == "hn":
        return widgets.hacker_news(count=int(data.get("count", 5)))
    if kind == "onthisday":
        return widgets.on_this_day(count=int(data.get("count", 4)))
    if kind == "calendar":
        year, month = data.get("year"), data.get("month")
        return widgets.calendar_month(
            year=int(year) if year else None,
            month=int(month) if month else None,
        )
    if kind == "countdown":
        return widgets.countdown(
            label=str(data.get("label", "")),
            target_iso=str(data.get("date", "")),
        )
    if kind == "habits":
        habits = data.get("habits") or []
        if not isinstance(habits, list):
            raise ValueError("invalid habits")
        return widgets.habit_tracker(
            habits=[str(h) for h in habits],
            days=int(data.get("days", 7)),
        )
    if kind == "advice":
        return widgets.advice()
    if kind == "briefing":
        location = (data.get("location") or "").strip()
        # One tall image is safe in the browser; printing still uses smaller
        # transfers to stay within the thermal printer's raster buffer.
        return widgets.morning_briefing(location=location)
    if kind == "ascii":
        return widgets.ascii_art(str(data.get("name", "")))
    if kind == "now":
        return widgets.now_card()
    raise ValueError(f"unknown widget: {kind}")


def _lab_body(kind: str, data: dict) -> str:
    """Build a lab receipt body with shared validation for preview and print."""
    if kind == "todo":
        title = (data.get("title") or "").strip()
        items = data.get("items") or []
        if not isinstance(items, list):
            raise ValueError("invalid items")
        if not any((item or "").strip() for item in items):
            raise ValueError("add at least one item")
        return widgets.todo(title, [str(item) for item in items])
    if kind == "receipt":
        items = data.get("items") or []
        if not items:
            raise ValueError("add at least one item")
        return widgets.receipt(
            store=data.get("store", ""),
            items=items,
            tax_rate=float(data.get("tax_rate", 0.0) or 0.0),
            note=data.get("note", ""),
        )
    if kind == "label":
        return widgets.label(
            text=data.get("text", ""),
            big=bool(data.get("big", True)),
        )
    raise ValueError(f"unknown lab: {kind}")


def _preview_markup(body: str) -> dict:
    img = render_feat.render_markup(body)
    return {
        "data_url": image_feat.to_png_data_url(img),
        "width": img.width,
        "height": img.height,
    }


@app.post("/api/admin/preview/widget/<kind>")
@require_admin
def preview_widget(kind: str):
    def run():
        data = request.get_json(silent=True) or {}
        return _preview_markup(_widget_body(kind, data))
    return _safe(run)


@app.post("/api/admin/preview/lab/<kind>")
@require_admin
def preview_lab(kind: str):
    def run():
        data = request.get_json(silent=True) or {}
        return _preview_markup(_lab_body(kind, data))
    return _safe(run)

@app.post("/api/admin/print/weather")
@require_admin
def print_weather():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(_widget_body("weather", data))
        return {}
    return _safe(run)


@app.post("/api/admin/print/dice")
@require_admin
def print_dice():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(_widget_body("dice", data))
        return {}
    return _safe(run)


@app.post("/api/admin/print/hn")
@require_admin
def print_hn():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(_widget_body("hn", data))
        return {}
    return _safe(run)


@app.post("/api/admin/print/onthisday")
@require_admin
def print_on_this_day():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(_widget_body("onthisday", data))
        return {}
    return _safe(run)


@app.post("/api/admin/print/calendar")
@require_admin
def print_calendar():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(_widget_body("calendar", data))
        return {}
    return _safe(run)


@app.post("/api/admin/print/countdown")
@require_admin
def print_countdown():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(_widget_body("countdown", data))
        return {}
    return _safe(run)


@app.post("/api/admin/print/habits")
@require_admin
def print_habits():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(_widget_body("habits", data))
        return {}
    return _safe(run)


@app.post("/api/admin/print/advice")
@require_admin
def print_advice():
    def run():
        _print_body(_widget_body("advice", {}))
        return {}
    return _safe(run)


@app.post("/api/admin/print/briefing")
@require_admin
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


@app.post("/api/admin/print/todo")
@require_admin
def print_todo():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(_lab_body("todo", data))
        return {}
    return _safe(run)


@app.post("/api/admin/print/receipt")
@require_admin
def print_receipt():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(_lab_body("receipt", data))
        return {}
    return _safe(run)


@app.post("/api/admin/print/label")
@require_admin
def print_label():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(_lab_body("label", data))
        return {}
    return _safe(run)


@app.post("/api/admin/print/ascii")
@require_admin
def print_ascii():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(_widget_body("ascii", data))
        return {}
    return _safe(run)


@app.post("/api/admin/print/now")
@require_admin
def print_now():
    def run():
        _print_body(_widget_body("now", {}))
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


@app.post("/api/admin/code/qr/preview")
@require_admin
def qr_preview():
    def run():
        opts = _qr_opts(request.get_json(silent=True) or {})
        img = codes_feat.make_qr_image(opts)
        return {"data_url": image_feat.to_png_data_url(img),
                "width": img.width, "height": img.height}
    return _safe(run)


@app.post("/api/admin/print/qr")
@require_admin
def print_qr():
    def run():
        opts = _qr_opts(request.get_json(silent=True) or {})
        if not opts.data:
            raise ValueError("enter QR data")
        with open_printer() as p:
            codes_feat.print_qr(p, opts)
            footer(p)
        return {}
    return _safe(run)


@app.post("/api/admin/code/barcode/preview")
@require_admin
def barcode_preview():
    def run():
        opts = _barcode_opts(request.get_json(silent=True) or {})
        img = codes_feat.make_barcode_image(opts)
        return {"data_url": image_feat.to_png_data_url(img),
                "width": img.width, "height": img.height}
    return _safe(run)


@app.post("/api/admin/print/barcode")
@require_admin
def print_barcode():
    def run():
        opts = _barcode_opts(request.get_json(silent=True) or {})
        if not opts.data:
            raise ValueError("enter barcode data")
        with open_printer() as p:
            codes_feat.print_barcode(p, opts)
            footer(p)
        return {}
    return _safe(run)


@app.get("/api/admin/code/barcode/types")
@require_admin
def barcode_types():
    return jsonify({"ok": True,
                    "types": list(codes_feat.BARCODE_TYPES.keys()),
                    "hri": codes_feat.HRI_POSITIONS})


# ---------- hardware controls ----------

@app.post("/api/admin/hw/cash_drawer")
@require_admin
def hw_cash_drawer():
    def run():
        data = request.get_json(silent=True) or {}
        pin = int(data.get("pin", 2))
        with open_printer() as p:
            hw_feat.cash_drawer(p, pin=pin)
        return {}
    return _safe(run)


@app.post("/api/admin/hw/beep")
@require_admin
def hw_beep():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.beep(p,
                         count=int(data.get("count", 1)),
                         duration_units=int(data.get("duration_units", 3)))
        return {}
    return _safe(run)


@app.post("/api/admin/hw/feed")
@require_admin
def hw_feed():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.feed_lines(p, int(data.get("lines", 3)))
        return {}
    return _safe(run)


@app.post("/api/admin/hw/cut")
@require_admin
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


@app.post("/api/admin/hw/reset")
@require_admin
def hw_reset():
    def run():
        with open_printer() as p:
            hw_feat.reset(p)
        return {}
    return _safe(run)


@app.post("/api/admin/hw/self_test")
@require_admin
def hw_self_test():
    def run():
        with open_printer() as p:
            hw_feat.self_test(p)
        return {}
    return _safe(run)


@app.post("/api/admin/hw/density")
@require_admin
def hw_density():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.set_density(p, int(data.get("level", 8)))
        return {}
    return _safe(run)


@app.post("/api/admin/hw/codepage")
@require_admin
def hw_codepage():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.set_code_page(p, int(data.get("n", 0)))
        return {}
    return _safe(run)


@app.get("/api/admin/hw/codepages")
@require_admin
def hw_codepages():
    return jsonify({
        "ok": True,
        "pages": [{"n": n, "label": label} for n, label in hw_feat.CODE_PAGES.items()],
    })


@app.post("/api/admin/hw/status")
@require_admin
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


@app.post("/api/admin/hw/raw")
@require_admin
def hw_raw():
    def run():
        data = request.get_json(silent=True) or {}
        text = data.get("bytes", "")
        parsed = hw_feat.parse_raw_input(text)
        if not parsed:
            raise ValueError("nothing to send")
        if len(parsed) > _MAX_RAW_BYTES:
            raise ValueError(f"{len(parsed)} bytes. limit: {_MAX_RAW_BYTES}")
        with open_printer() as p:
            hw_feat.send_bytes(p, parsed)
        return {"sent": len(parsed)}
    return _safe(run)


@app.get("/api/admin/hw/led/protocols")
@require_admin
def hw_led_protocols():
    return jsonify({
        "ok": True,
        "protocols": [
            {"key": p.key, "name": p.name, "note": p.note}
            for p in led_feat.PROTOCOLS.values()
        ],
    })


@app.post("/api/admin/hw/led/preview")
@require_admin
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


@app.post("/api/admin/hw/led")
@require_admin
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


@app.get("/api/admin/hw/cheatsheet")
@require_admin
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

# Friend-message print queue. POST /api/print enqueues and returns
# immediately; a single daemon worker drains in FIFO order so two friends
# hitting send at the same time both get an instant "queued" instead of
# one of them blocking on the USB lock for the duration of the other's
# print. Replaces the per-user 10s rate limit that used to reject bursts.
#
# The history row is the job. Enqueue stores what the friend sent (raw
# body or processed drawing, anonymous flag) and puts only the ids on
# the queue; the worker rebuilds the print from the row when it gets
# there. That makes the queue survive restarts for free: rows still
# 'queued' at boot are pushed back onto the in-memory queue by
# _replay_queued() below. One caveat — a job that was mid-print when
# the process died is replayed too, so a crash at exactly the wrong
# moment can print a receipt twice. Preferable to losing it.
#
# Cap exists so a runaway client can't pin unbounded memory; on overflow
# we return 503 and the friend can retry once the printer catches up.
# Single-process only — load-bearing alongside `gunicorn --workers 1`
# (a second worker would replay the same rows and print them twice).
_PRINT_QUEUE_MAX = 50
_PRINT_QUEUE: "queue.Queue[tuple[int, int]]" = queue.Queue(maxsize=_PRINT_QUEUE_MAX)

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


def _friend_job(msg: dict) -> dict:
    """Rebuild a friend's print from its history row, the same way for a
    fresh send, a replay after restart, and an owner retry. Name style is
    whatever the friend has picked by print time."""
    style = msg["name_style"] or "plain"
    # SQLite stamps printed_at in UTC; the footer wants local wall time.
    sent = (
        datetime.fromisoformat(msg["printed_at"])
        .replace(tzinfo=timezone.utc)
        .astimezone()
    )
    if msg["drawing"] is not None:
        header, footer_markup = widgets.friend_frame(
            msg["username"], style=style, anonymous=msg["anonymous"], when=sent,
        )
        return {
            "kind": "doodle",
            "image": Image.open(io.BytesIO(msg["drawing"])),
            "header": header, "footer": footer_markup,
        }
    return {"kind": "text", "body": widgets.friend_message(
        msg["username"], msg["body"], style=style,
        anonymous=msg["anonymous"], when=sent,
    )}


def _print_friend_job(job: dict) -> None:
    if job["kind"] == "doodle":
        _print_doodle(job)
    else:
        _print_body(job["body"])


def _print_worker() -> None:
    while True:
        user_id, msg_id = _PRINT_QUEUE.get()
        status = "printed"
        try:
            msg = auth_db.get_message(msg_id)
            if msg is None:
                # The friend (and their rows, via ON DELETE CASCADE) went
                # away while this sat in the queue. Nothing to print.
                raise LookupError(f"message {msg_id} no longer exists")
            _print_friend_job(_friend_job(msg))
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


def _replay_queued() -> int:
    """Push rows left 'queued' by a previous process back onto the queue.
    Runs once at import, before the worker starts and before any request
    can enqueue, so ordering is just row id. Returns how many it queued."""
    n = 0
    for user_id, msg_id in auth_db.list_queued_message_ids():
        try:
            _PRINT_QUEUE.put_nowait((user_id, msg_id))
        except queue.Full:
            # More leftovers than slots — only possible if the cap was
            # lowered between runs. Fail the rest honestly so they get a
            # retry button instead of sitting 'queued' forever.
            auth_db.set_message_status(msg_id, "failed")
            continue
        with _inflight_lock:
            _inflight[user_id] = _inflight.get(user_id, 0) + 1
        n += 1
    if n:
        print(f"[queue] replaying {n} print(s) left queued by the last run",
              file=sys.stderr, flush=True)
    return n


_replay_queued()
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


@app.post("/api/preview")
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
            raise ValueError(f"{_MAX_MSG_LEN} character limit")
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


def _enqueue_friend_print(
    user: dict,
    body: str,
    drawing: bytes | None = None,
    anonymous: bool = False,
):
    """Shared bookkeeping for every friend print kind: per-user cap,
    history row (which is also the job — see _PRINT_QUEUE), queue
    insert, and the crash-safe unwind. Returns a Flask response."""
    with _inflight_lock:
        if _inflight.get(user["id"], 0) >= _PER_USER_QUEUE_CAP:
            return jsonify({
                "ok": False,
                "error": f"{_PER_USER_QUEUE_CAP} prints already queued",
                "kind": "user_cap",
            }), 429
        _inflight[user["id"]] = _inflight.get(user["id"], 0) + 1

    msg_id = None
    try:
        # Log to history at enqueue time so the friend sees their message
        # immediately, even before the worker actually pulls it. The row
        # starts 'queued'; the worker flips it to 'printed' or 'failed' so
        # the friend can see whether it actually hit paper.
        msg_id = auth_db.log_message(
            user["id"], body, status="queued", drawing=drawing,
            anonymous=anonymous,
        )

        # qsize() before put = jobs the printer must finish first.
        # Approximate (the worker may have started one but not yet
        # decremented), but close enough for a UI hint.
        ahead = _PRINT_QUEUE.qsize()
        _PRINT_QUEUE.put_nowait((user["id"], msg_id))
    except queue.Full:
        _dec_inflight(user["id"])
        auth_db.delete_message(msg_id)  # never entered the queue
        return jsonify({
            "ok": False,
            "error": "print queue full. try again soon",
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
        return jsonify({"ok": False, "error": "print failed",
                        "kind": "server"}), 500

    return jsonify({"ok": True, "queued": True, "ahead": ahead})


@app.post("/api/print")
@require_allowed
def friend_print():
    user = current_user()
    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"ok": False, "error": "type something"}), 400
    if len(body) > _MAX_MSG_LEN:
        return jsonify({"ok": False, "error": f"{_MAX_MSG_LEN} character limit"}), 400

    return _enqueue_friend_print(
        user, body, anonymous=bool(data.get("anonymous", False)),
    )


@app.post("/api/print/doodle")
@require_allowed
def friend_print_doodle():
    user = current_user()
    data = request.get_json(silent=True) or {}
    raw = data.get("image") or ""
    if not isinstance(raw, str) or not raw.startswith(_DOODLE_PREFIX):
        return jsonify({"ok": False, "error": "drawing missing",
                        "kind": "input"}), 400
    try:
        png = base64.b64decode(raw[len(_DOODLE_PREFIX):], validate=True)
    except binascii.Error:
        return jsonify({"ok": False, "error": "invalid image data",
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
    saved = io.BytesIO()
    img.save(saved, format="PNG")
    return _enqueue_friend_print(
        user, "(doodle)", drawing=saved.getvalue(),
        anonymous=bool(data.get("anonymous", False)),
    )


@app.get("/api/history")
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


@app.get("/api/history/<int:message_id>/drawing")
@require_allowed
def friend_history_drawing(message_id: int):
    """Return one reusable drawing from the signed-in friend's history."""
    user = current_user()
    drawing = auth_db.get_message_drawing_for_user(message_id, user["id"])
    if drawing is None:
        return jsonify({"ok": False, "error": "drawing not found"}), 404
    encoded = base64.b64encode(drawing).decode("ascii")
    return jsonify({"ok": True, "image": _DOODLE_PREFIX + encoded})


@app.get("/api/printer")
@require_allowed
def friend_printer_status():
    """Last-known printer reachability for the soft banner on the friends page.

    Deliberately coarse: True until a real USB open fails, False until a
    print completes. Queueing is unaffected — the banner only sets
    expectations."""
    return jsonify({"ok": True, "printer": printer_status()})


# ---------- admin: friend management ----------

@app.get("/api/admin/users")
@require_admin
def admin_list_users():
    status = request.args.get("status")
    try:
        users = auth_db.list_users(status=status)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "users": users})


@app.post("/api/admin/users/<int:user_id>/approve")
@require_admin
def admin_approve_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "user not found"}), 404
    auth_db.set_status(user_id, "allowed")
    return jsonify({"ok": True})


@app.post("/api/admin/users/<int:user_id>/revoke")
@require_admin
def admin_revoke_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "user not found"}), 404
    auth_db.set_status(user_id, "blocked")
    return jsonify({"ok": True})


@app.post("/api/admin/users/<int:user_id>/delete")
@require_admin
def admin_delete_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "user not found"}), 404
    auth_db.delete_user(user_id)
    return jsonify({"ok": True})


@app.post("/api/admin/users/<int:user_id>/reset_link")
@require_admin
def admin_reset_link(user_id: int):
    """Mint a temporary forgot-password link for a friend. There's no
    self-service reset on the friends page — the owner hands this link
    over whatever chat they already share, and the friend picks their own
    new password there, so the owner never knows or types it. The token
    rides in the URL fragment (not a query string) so it never shows up
    in access logs; the console builds the absolute URL client-side."""
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "user not found"}), 404
    token = auth_db.create_reset_token(user_id)
    return jsonify({
        "ok": True,
        "path": f"/#reset={token}",
        "expires_minutes": auth_db.RESET_TOKEN_MINUTES,
    })


@app.get("/api/admin/messages")
@require_admin
def admin_list_messages():
    try:
        limit = max(1, min(200, int(request.args.get("limit", 20))))
    except (TypeError, ValueError):
        limit = 20
    return jsonify({"ok": True, "messages": auth_db.list_messages(limit=limit)})


@app.post("/api/admin/messages/<int:msg_id>/retry")
@require_admin
def admin_retry_message(msg_id: int):
    """Reprint a friend message the queue worker marked 'failed'.

    Synchronous like every other owner print, not re-queued: the owner
    is usually standing at the printer when they hit retry, and wants
    "printer offline" in the toast now rather than a row that quietly
    stays red. Same job builder as the worker, so the paper comes out
    identical to what the friend would have gotten.
    """
    def run():
        msg = auth_db.get_message(msg_id)
        if msg is None:
            raise ValueError("no such message")
        if msg["status"] != "failed":
            raise ValueError("only failed prints can be retried")
        _print_friend_job(_friend_job(msg))
        auth_db.set_message_status(msg_id, "printed")
    return _safe(run)


@app.post("/api/admin/printer/reset")
@require_admin
def admin_reset_printer():
    """Issue a USB port reset to the printer — software unplug-replug."""
    if config.DRY_RUN:
        return jsonify({"ok": True, "reset": False, "dry_run": True})
    found = reset_device()
    if not found:
        return jsonify({"ok": False, "error": "printer not found on USB"}), 503
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
    # the Pi — the app is public at print.cuzeth.com and the debugger = RCE.
    app.run(host=config.HOST, port=config.PORT, debug=os.environ.get("FLASK_DEBUG") == "1")
