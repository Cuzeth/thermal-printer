"""Image-processing pipeline tests. Shape + mode only — pixel-exact would
depend on Pillow version."""

from __future__ import annotations

import io

from PIL import Image

import config
from features import image as image_feat


def _png_bytes(w: int, h: int, color=(128, 128, 128)) -> bytes:
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_dither_produces_1bit():
    data = _png_bytes(200, 100)
    out = image_feat.process(data, image_feat.ProcessOptions(mode="dither"))
    assert out.mode == "1"


def test_threshold_produces_1bit():
    data = _png_bytes(200, 100)
    out = image_feat.process(data, image_feat.ProcessOptions(mode="threshold", threshold=64))
    assert out.mode == "1"


def test_grayscale_keeps_L():
    data = _png_bytes(200, 100)
    out = image_feat.process(data, image_feat.ProcessOptions(mode="grayscale"))
    assert out.mode == "L"


def test_resize_clamps_to_printer_width():
    # Asking for more than printer width should be clamped.
    data = _png_bytes(1000, 500)
    opts = image_feat.ProcessOptions(width=config.PRINTER_PIXEL_WIDTH * 10)
    out = image_feat.process(data, opts)
    assert out.width <= config.PRINTER_PIXEL_WIDTH


def test_resize_preserves_aspect_ratio():
    data = _png_bytes(400, 200)  # 2:1 ratio
    opts = image_feat.ProcessOptions(width=200)
    out = image_feat.process(data, opts)
    assert out.width == 200
    # Allow off-by-one from rounding.
    assert 99 <= out.height <= 101


def test_pad_to_printer_width_centers_narrow_image():
    narrow = Image.new("1", (100, 80), 1)
    padded = image_feat.pad_to_printer_width(narrow)
    assert padded.width == config.PRINTER_PIXEL_WIDTH
    assert padded.height == 80


def test_pad_is_noop_when_already_full_width():
    full = Image.new("1", (config.PRINTER_PIXEL_WIDTH, 40), 1)
    padded = image_feat.pad_to_printer_width(full)
    assert padded is full


def test_png_data_url_roundtrip():
    data = _png_bytes(100, 100)
    img = image_feat.process(data, image_feat.ProcessOptions(mode="dither"))
    url = image_feat.to_png_data_url(img)
    assert url.startswith("data:image/png;base64,")
