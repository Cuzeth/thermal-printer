"""Thermal printer master GUI — Flask backend.

Run with:  python3 app.py
Open:      http://127.0.0.1:5005
"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from flask import Flask, jsonify, render_template, request

import config
from features import image as image_feat
from features import render as render_feat
from features import text as text_feat
from features import widgets
from printer import PrinterError, footer, open_printer


app = Flask(__name__)


@app.route("/")
def index():
    return render_template(
        "index.html",
        width=config.RECEIPT_WIDTH,
        pixel_width=config.PRINTER_PIXEL_WIDTH,
        dry_run=config.DRY_RUN,
    )


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


# ---------- health ----------

@app.get("/api/ping")
def ping():
    return jsonify({"ok": True, "dry_run": config.DRY_RUN})


if __name__ == "__main__":
    print(f"Thermal Printer GUI -> http://{config.HOST}:{config.PORT}")
    if config.DRY_RUN:
        print(f"DRY RUN mode: bytes will be written to {config.DRY_RUN_PATH}")
    app.run(host=config.HOST, port=config.PORT, debug=True)
