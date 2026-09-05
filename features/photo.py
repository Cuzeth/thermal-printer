"""Small, bounded photo strips for friends, saved as final thermal pixels.

The browser crops first to spare the Pi large phone uploads. These checks still
stand alone: preview and print are public routes behind friend approval, so a
custom client must not turn four frames into an unbounded raster allocation.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps

import config
from features import image as image_feat
from features import render as render_feat


MAX_FRAMES = 4
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_CAPTION = 160
FORMATS = {"JPEG", "PNG", "WEBP"}
TREATMENTS = {
    "soft": {"mode": "dither", "brightness": 1.12, "contrast": 1.05},
    "contrast": {"mode": "dither", "brightness": 1.05, "contrast": 1.65},
    "ink": {"mode": "threshold", "contrast": 1.4, "threshold": 150},
}


def _frame(raw: bytes, treatment: str, side: int) -> Image.Image:
    if not raw:
        raise ValueError("choose a photo")
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("each photo must be 4 MB or smaller")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in FORMATS:
                raise ValueError("use JPEG, PNG or WebP photos")
            if source.width * source.height > image_feat.MAX_INPUT_PIXELS:
                raise ValueError("photo exceeds the 30 million pixel limit")
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("use a still photo, not an animation")
            # Rotate before cropping so direct API uploads agree with phone
            # orientation; browser-cropped PNGs have no EXIF to apply twice.
            oriented = ImageOps.exif_transpose(source)
            cropped = ImageOps.fit(oriented, (side, side), method=Image.Resampling.LANCZOS)
            normalized = io.BytesIO()
            cropped.save(normalized, format="PNG")
        return image_feat.process(
            normalized.getvalue(),
            image_feat.ProcessOptions(width=side, **TREATMENTS[treatment]),
        )
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("photo could not be opened; use JPEG, PNG or WebP") from exc


def render_strip(photos: list[bytes], treatment: str = "soft", caption: str = "") -> Image.Image:
    """Return the same bounded 1-bit strip for preview, send and durable reuse."""
    if not 1 <= len(photos) <= MAX_FRAMES:
        raise ValueError("choose between 1 and 4 photos")
    if treatment not in TREATMENTS:
        raise ValueError("choose soft, contrast or ink")
    if len(caption) > MAX_CAPTION:
        raise ValueError("160 character caption limit")
    # A caption stays on the strip, never becoming an extra cut or receipt.
    caption = " ".join(caption.split())
    width = config.PRINTER_PIXEL_WIDTH
    margin = max(8, width // 24)
    gap = max(8, width // 36)
    side = width - margin * 2
    title = render_feat.render_markup("> PHOTO BOOTH")
    frames = [_frame(raw, treatment, side) for raw in photos]
    label = render_feat.render_markup("> " + caption) if caption else None
    height = title.height + gap + len(frames) * (side + gap)
    if label is not None:
        height += label.height
    if height > image_feat.MAX_OUTPUT_HEIGHT:
        raise ValueError("photo strip exceeds the printer height limit")
    strip = Image.new("1", (width, height), 1)
    strip.paste(title.convert("1"), (0, 0))
    y = title.height + gap
    for frame in frames:
        strip.paste(frame, (margin, y))
        y += side + gap
    if label is not None:
        strip.paste(label.convert("1"), (0, y))
    return strip
