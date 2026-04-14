"""Image processing for thermal printing.

Takes any uploaded image, resizes it to the printer width, converts to
grayscale, optionally applies contrast/brightness, and dithers to 1-bit
black & white using Floyd-Steinberg. Returns the PIL Image ready to pass
to python-escpos's `.image()`.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from PIL import Image, ImageEnhance, ImageOps

import config


@dataclass
class ProcessOptions:
    width: int = config.PRINTER_PIXEL_WIDTH
    contrast: float = 1.0      # 1.0 = no change
    brightness: float = 1.0    # 1.0 = no change
    invert: bool = False
    mode: str = "dither"       # "dither" | "threshold" | "grayscale"
    threshold: int = 128       # for "threshold" mode, 0-255


def process(image_bytes: bytes, opts: ProcessOptions) -> Image.Image:
    img = Image.open(io.BytesIO(image_bytes))

    # Handle transparency -> white background
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")

    # Resize proportionally to the target width
    target_w = max(8, min(opts.width, config.PRINTER_PIXEL_WIDTH))
    if img.width != target_w:
        ratio = target_w / img.width
        new_h = max(1, int(round(img.height * ratio)))
        img = img.resize((target_w, new_h), Image.LANCZOS)

    # Grayscale
    gray = ImageOps.grayscale(img)

    if opts.brightness != 1.0:
        gray = ImageEnhance.Brightness(gray).enhance(opts.brightness)
    if opts.contrast != 1.0:
        gray = ImageEnhance.Contrast(gray).enhance(opts.contrast)
    if opts.invert:
        gray = ImageOps.invert(gray)

    if opts.mode == "grayscale":
        return gray
    if opts.mode == "threshold":
        t = max(0, min(255, int(opts.threshold)))
        return gray.point(lambda v: 255 if v >= t else 0, mode="1")
    # default: Floyd-Steinberg dither
    return gray.convert("1")


def to_png_data_url(img: Image.Image) -> str:
    """Serialize a PIL image as a data: URL for the web preview."""
    if img.mode == "1":
        display = img.convert("L")
    else:
        display = img
    buf = io.BytesIO()
    display.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"
