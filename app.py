"""Thermal printer master GUI — Flask backend.

Run with:  python3 app.py
Open:      http://127.0.0.1:5005
"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from flask import Flask, jsonify, render_template, request

import config
from auth import auth_bp
from auth import db as auth_db
from auth.session import current_user, require_admin, require_allowed
from features import codes as codes_feat
from features import hardware as hw_feat
from features import image as image_feat
from features import led as led_feat
from features import render as render_feat
from features import text as text_feat
from features import widgets
from printer import PrinterError, footer, open_printer


app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.register_blueprint(auth_bp)
auth_db.init()


@app.route("/")
def index():
    return render_template(
        "index.html",
        width=config.RECEIPT_WIDTH,
        pixel_width=config.PRINTER_PIXEL_WIDTH,
        dry_run=config.DRY_RUN,
        admin_token=config.ADMIN_TOKEN,
    )


@app.route("/m/")
@app.route("/m")
def friends_index():
    return render_template("friends.html")


# ---------- generic body-printer helper ----------

def _print_rich(p, body: str) -> None:
    """Render markup as an image and print each `!!!`-separated segment."""
    segments = render_feat.split_cuts(body) or [body]
    for i, seg in enumerate(segments):
        img = render_feat.render_markup(seg)
        p.image(img)
        if i < len(segments) - 1:
            p.cut()


def _print_body(body: str, cut: bool = True, rich: bool = True) -> None:
    with open_printer() as p:
        if rich:
            _print_rich(p, body)
        else:
            text_feat.render(p, body)
        if cut:
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
def preview():
    body = (request.get_json(silent=True) or {}).get("body", "")
    return jsonify({"ok": True, "preview": text_feat.preview(body), "width": config.RECEIPT_WIDTH})


@app.post("/api/preview/rich")
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

@app.post("/api/image/preview")
def image_preview():
    def run():
        if "file" not in request.files:
            raise ValueError("No file uploaded.")
        f = request.files["file"]
        opts = image_feat.ProcessOptions(
            width=int(request.form.get("width", config.PRINTER_PIXEL_WIDTH)),
            contrast=float(request.form.get("contrast", 1.0)),
            brightness=float(request.form.get("brightness", 1.0)),
            invert=request.form.get("invert", "false").lower() == "true",
            mode=request.form.get("mode", "dither"),
            threshold=int(request.form.get("threshold", 128)),
        )
        img = image_feat.process(f.read(), opts)
        return {"data_url": image_feat.to_png_data_url(img),
                "width": img.width, "height": img.height}
    return _safe(run)


@app.post("/api/print/image")
def print_image():
    def run():
        if "file" not in request.files:
            raise ValueError("No file uploaded.")
        f = request.files["file"]
        opts = image_feat.ProcessOptions(
            width=int(request.form.get("width", config.PRINTER_PIXEL_WIDTH)),
            contrast=float(request.form.get("contrast", 1.0)),
            brightness=float(request.form.get("brightness", 1.0)),
            invert=request.form.get("invert", "false").lower() == "true",
            mode=request.form.get("mode", "dither"),
            threshold=int(request.form.get("threshold", 128)),
        )
        caption = request.form.get("caption", "").strip()
        img = image_feat.process(f.read(), opts)
        img = image_feat.pad_to_printer_width(img)
        with open_printer() as p:
            p.image(img)
            if caption:
                p.text("\n")
                _print_rich(p, f"> {caption}")
            footer(p)
        return {}
    return _safe(run)


# ---------- widget routes ----------

@app.post("/api/print/quote")
def print_quote():
    return _safe(lambda: (_print_body(widgets.random_quote()), {})[1])


@app.post("/api/print/joke")
def print_joke():
    return _safe(lambda: (_print_body(widgets.dad_joke()), {})[1])


@app.post("/api/print/haiku")
def print_haiku():
    return _safe(lambda: (_print_body(widgets.haiku()), {})[1])


@app.post("/api/print/eight_ball")
def print_eight_ball():
    def run():
        q = (request.get_json(silent=True) or {}).get("question", "")
        _print_body(widgets.magic_eight_ball(q))
        return {}
    return _safe(run)


@app.post("/api/print/weather")
def print_weather():
    def run():
        loc = (request.get_json(silent=True) or {}).get("location", "").strip()
        if not loc:
            raise ValueError("location is required")
        _print_body(widgets.weather(loc))
        return {}
    return _safe(run)


@app.post("/api/print/dice")
def print_dice():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(widgets.roll_dice(
            count=int(data.get("count", 2)),
            sides=int(data.get("sides", 6)),
        ))
        return {}
    return _safe(run)


@app.post("/api/print/todo")
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
def print_ascii():
    def run():
        data = request.get_json(silent=True) or {}
        _print_body(widgets.ascii_art(data.get("name", "")))
        return {}
    return _safe(run)


@app.post("/api/print/now")
def print_now():
    return _safe(lambda: (_print_body(widgets.now_card()), {})[1])


# ---------- codes (QR / barcodes) ----------

@app.post("/api/code/qr/preview")
def qr_preview():
    def run():
        data = request.get_json(silent=True) or {}
        opts = codes_feat.QROptions(
            data=data.get("data", ""),
            ec=data.get("ec", "M"),
            size=int(data.get("size", 8)),
            box_size=int(data.get("box_size", 10)),
        )
        img = codes_feat.make_qr_image(opts)
        return {"data_url": image_feat.to_png_data_url(img),
                "width": img.width, "height": img.height}
    return _safe(run)


@app.post("/api/print/qr")
def print_qr():
    def run():
        data = request.get_json(silent=True) or {}
        opts = codes_feat.QROptions(
            data=data.get("data", ""),
            ec=data.get("ec", "M"),
            size=int(data.get("size", 8)),
        )
        if not opts.data:
            raise ValueError("QR payload is empty.")
        with open_printer() as p:
            codes_feat.print_qr(p, opts)
            footer(p)
        return {}
    return _safe(run)


@app.post("/api/code/barcode/preview")
def barcode_preview():
    def run():
        data = request.get_json(silent=True) or {}
        opts = codes_feat.BarcodeOptions(
            kind=data.get("kind", "CODE128"),
            data=data.get("data", ""),
            width=int(data.get("width", 3)),
            height=int(data.get("height", 80)),
            hri=data.get("hri", "BELOW"),
            font=data.get("font", "A"),
        )
        img = codes_feat.make_barcode_image(opts)
        return {"data_url": image_feat.to_png_data_url(img),
                "width": img.width, "height": img.height}
    return _safe(run)


@app.post("/api/print/barcode")
def print_barcode():
    def run():
        data = request.get_json(silent=True) or {}
        opts = codes_feat.BarcodeOptions(
            kind=data.get("kind", "CODE128"),
            data=data.get("data", ""),
            width=int(data.get("width", 3)),
            height=int(data.get("height", 80)),
            hri=data.get("hri", "BELOW"),
            font=data.get("font", "A"),
        )
        if not opts.data:
            raise ValueError("Barcode payload is empty.")
        with open_printer() as p:
            codes_feat.print_barcode(p, opts)
            footer(p)
        return {}
    return _safe(run)


@app.get("/api/code/barcode/types")
def barcode_types():
    return jsonify({"ok": True,
                    "types": list(codes_feat.BARCODE_TYPES.keys()),
                    "hri": codes_feat.HRI_POSITIONS})


# ---------- hardware controls ----------

@app.post("/api/hw/cash_drawer")
def hw_cash_drawer():
    def run():
        data = request.get_json(silent=True) or {}
        pin = int(data.get("pin", 2))
        with open_printer() as p:
            hw_feat.cash_drawer(p, pin=pin)
        return {}
    return _safe(run)


@app.post("/api/hw/beep")
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
def hw_feed():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.feed_lines(p, int(data.get("lines", 3)))
        return {}
    return _safe(run)


@app.post("/api/hw/cut")
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
def hw_reset():
    def run():
        with open_printer() as p:
            hw_feat.reset(p)
        return {}
    return _safe(run)


@app.post("/api/hw/self_test")
def hw_self_test():
    def run():
        with open_printer() as p:
            hw_feat.self_test(p)
        return {}
    return _safe(run)


@app.post("/api/hw/density")
def hw_density():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.set_density(p, int(data.get("level", 8)))
        return {}
    return _safe(run)


@app.post("/api/hw/codepage")
def hw_codepage():
    def run():
        data = request.get_json(silent=True) or {}
        with open_printer() as p:
            hw_feat.set_code_page(p, int(data.get("n", 0)))
        return {}
    return _safe(run)


@app.get("/api/hw/codepages")
def hw_codepages():
    return jsonify({
        "ok": True,
        "pages": [{"n": n, "label": label} for n, label in hw_feat.CODE_PAGES.items()],
    })


@app.post("/api/hw/status")
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


@app.post("/api/hw/raw")
def hw_raw():
    def run():
        data = request.get_json(silent=True) or {}
        text = data.get("bytes", "")
        with open_printer() as p:
            n = hw_feat.send_raw(p, text)
        return {"sent": n}
    return _safe(run)


@app.get("/api/hw/led/protocols")
def hw_led_protocols():
    return jsonify({
        "ok": True,
        "protocols": [
            {"key": p.key, "name": p.name, "note": p.note}
            for p in led_feat.PROTOCOLS.values()
        ],
    })


@app.post("/api/hw/led/preview")
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
def hw_led():
    def run():
        data = request.get_json(silent=True) or {}
        protocol = data.get("protocol", "esc_c")
        r = int(data.get("r", 0))
        g = int(data.get("g", 0))
        b = int(data.get("b", 0))
        blink = bool(data.get("blink", False))
        with open_printer() as p:
            bs = led_feat.send_color(p, protocol, r, g, b)
            if blink:
                import time
                time.sleep(0.25)
                led_feat.send_color(p, protocol, 0, 0, 0)
                time.sleep(0.25)
                led_feat.send_color(p, protocol, r, g, b)
        return {"bytes": led_feat.hex_preview(bs)}
    return _safe(run)


@app.get("/api/hw/cheatsheet")
def hw_cheatsheet():
    return jsonify({
        "ok": True,
        "entries": [
            {"name": n, "hex": h, "desc": d}
            for n, h, d in hw_feat.CHEAT_SHEET
        ],
    })


# ---------- friend message endpoint ----------

# In-memory rate limit: {user_id: last_print_unix_ts}. Resets on container
# restart, which is fine — friends are a small trusted group post-approval.
_LAST_PRINT: dict[int, float] = {}
_RATE_LIMIT_SECONDS = 10
_MAX_MSG_LEN = 800


@app.post("/api/m/print")
@require_allowed
def friend_print():
    import time

    user = current_user()
    body = ((request.get_json(silent=True) or {}).get("body") or "").strip()
    if not body:
        return jsonify({"ok": False, "error": "message is empty"}), 400
    if len(body) > _MAX_MSG_LEN:
        return jsonify({"ok": False, "error": f"message too long (max {_MAX_MSG_LEN} chars)"}), 400

    now = time.time()
    last = _LAST_PRINT.get(user["id"], 0)
    wait = _RATE_LIMIT_SECONDS - (now - last)
    if wait > 0:
        return jsonify({
            "ok": False,
            "error": f"slow down — try again in {int(wait) + 1}s",
            "kind": "rate_limit",
        }), 429

    formatted = widgets.friend_message(user["username"], body)
    try:
        _print_body(formatted)
    except PrinterError as e:
        return jsonify({"ok": False, "error": str(e), "kind": "printer"}), 503

    _LAST_PRINT[user["id"]] = now
    auth_db.log_message(user["id"], body)
    return jsonify({"ok": True})


# ---------- admin (Bearer-token gated) ----------

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
        return jsonify({"ok": False, "error": "no such user"}), 404
    auth_db.set_status(user_id, "allowed")
    return jsonify({"ok": True})


@app.post("/api/admin/users/<int:user_id>/revoke")
@require_admin
def admin_revoke_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "no such user"}), 404
    auth_db.set_status(user_id, "blocked")
    return jsonify({"ok": True})


@app.post("/api/admin/users/<int:user_id>/delete")
@require_admin
def admin_delete_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "no such user"}), 404
    auth_db.delete_user(user_id)
    return jsonify({"ok": True})


@app.get("/api/admin/messages")
@require_admin
def admin_list_messages():
    limit = max(1, min(200, int(request.args.get("limit", 20))))
    return jsonify({"ok": True, "messages": auth_db.list_messages(limit=limit)})


# ---------- health ----------

@app.get("/api/ping")
def ping():
    return jsonify({"ok": True, "dry_run": config.DRY_RUN})


def _print_banner() -> None:
    print(f"Thermal Printer GUI -> http://{config.HOST}:{config.PORT}")
    if config.DRY_RUN:
        print(f"DRY RUN mode: bytes will be written to {config.DRY_RUN_PATH}")
    if not config._ADMIN_TOKEN_FROM_ENV:
        print(f"DEV ADMIN_TOKEN={config.ADMIN_TOKEN}  (set ADMIN_TOKEN in env to persist)")


_print_banner()


if __name__ == "__main__":
    # Dev entrypoint. In production gunicorn imports `app` directly and the
    # banner prints on the first import above.
    app.run(host=config.HOST, port=config.PORT, debug=True)
