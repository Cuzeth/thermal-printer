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


# Hard ceiling on the processed image height. Width is clamped to the
# printer, but height scales proportionally — a 100×20,000 upload would
# become 576×115,200 after resize: an OOM candidate on a Pi Zero's 512MB
# and ~14 meters of paper. 4096px ≈ half a meter of receipt.
MAX_OUTPUT_HEIGHT = 4096

# Ceiling on *input* pixels, checked before any decode/convert work.
# Image.open() only reads the header, so width/height are known cheaply;
# .convert("RGB") is what actually materializes the bitmap (3 bytes per
# pixel — 30M px ≈ 90MB, about the most a small Pi should be asked to
# hold for a hobby print). Any phone photo fits comfortably.
MAX_INPUT_PIXELS = 30_000_000


@dataclass
class ProcessOptions:
    width: int = config.PRINTER_PIXEL_WIDTH
    contrast: float = 1.0      # 1.0 = no change
    brightness: float = 1.0    # 1.0 = no change
    invert: bool = False
    mode: str = "dither"       # "dither" | "threshold" | "grayscale"
    threshold: int = 128       # for "threshold" mode, 0-255


def process(image_bytes: bytes, opts: ProcessOptions) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        # PIL raises UnidentifiedImageError (an OSError) on non-image
        # bytes. Surface it as input error (400), not a server 500.
        raise ValueError("not an image") from e
    if img.width < 1 or img.height < 1:
        raise ValueError("empty image")
    if img.width * img.height > MAX_INPUT_PIXELS:
        raise ValueError(
            f"image too large: {img.width}×{img.height}. "
            f"limit: {MAX_INPUT_PIXELS:,} pixels"
        )

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
    new_h = img.height
    if img.width != target_w:
        ratio = target_w / img.width
        new_h = max(1, int(round(img.height * ratio)))
    if new_h > MAX_OUTPUT_HEIGHT:
        raise ValueError(
            f"output height: {new_h}px. limit: {MAX_OUTPUT_HEIGHT}px. "
            "crop the image or reduce its width"
        )
    if img.width != target_w:
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


def pad_to_printer_width(img: Image.Image) -> Image.Image:
    """Center a narrower image on a full-width white canvas so we don't rely
    on the printer's own `align="center"` (which needs a profile with
    media.width.pixel set — not always configured)."""
    target = config.PRINTER_PIXEL_WIDTH
    if img.width >= target:
        return img
    fill = 1 if img.mode == "1" else 255
    canvas = Image.new(img.mode, (target, img.height), fill)
    canvas.paste(img, ((target - img.width) // 2, 0))
    return canvas
