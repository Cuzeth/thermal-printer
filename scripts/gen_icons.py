"""Render the raster favicons from the same geometry as static/favicon.svg.

The SVG covers modern Chrome/Firefox tabs; everything else still wants
bitmaps — Safari ignores SVG favicons entirely, iOS home screens want a
full-bleed apple-touch-icon, and Android's install prompt reads PNG sizes
out of the manifest. Rather than check in binaries nobody can diff, this
script redraws the icon with PIL and writes every size. Rerun it whenever
favicon.svg changes and commit the outputs together:

    .venv/bin/python scripts/gen_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

STATIC = Path(__file__).resolve().parents[1] / "static"

# Palette lifted from style.css. The tile sits between --bg and --panel so
# the icon holds its own against both light and dark browser chrome.
TILE = "#12141b"
PAPER = "#f5efd8"
PAPER_INK = "#1a1915"
ACCENT = "#f7b42c"

# Everything is drawn in the SVG's 64-unit space and scaled up; keep these
# numbers in lockstep with favicon.svg or the two icons will drift apart.
RECEIPT = [
    (18, 9), (46, 9), (46, 47),
    (42.5, 53), (39, 47), (35.5, 53), (32, 47),
    (28.5, 53), (25, 47), (21.5, 53), (18, 47),
]
LINES = [
    # (x, y, width, height, color)
    (22, 14, 20, 4, ACCENT),
    (22, 22, 20, 3, PAPER_INK),
    (22, 28, 14, 3, PAPER_INK),
    (22, 34, 20, 3, PAPER_INK),
    (22, 40, 9, 3, PAPER_INK),
    (36, 40, 6, 3, ACCENT),
]

# Supersample: draw huge, shrink with LANCZOS. PIL has no anti-aliased
# polygon fill, so this is what keeps the zigzag from looking like stairs.
CANVAS = 1024
S = CANVAS / 64


def draw_icon(rounded: bool) -> Image.Image:
    """Rounded = transparent-corner tab icon; full-bleed for home screens
    (iOS and Android mask their own corner radius onto the square)."""
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle([0, 0, CANVAS - 1, CANVAS - 1], radius=14 * S, fill=TILE)
    else:
        d.rectangle([0, 0, CANVAS, CANVAS], fill=TILE)
    d.polygon([(x * S, y * S) for x, y in RECEIPT], fill=PAPER)
    for x, y, w, h, color in LINES:
        d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S], radius=S, fill=color)
    return img


def shrink(img: Image.Image, size: int) -> Image.Image:
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    tile = draw_icon(rounded=True)
    fullbleed = draw_icon(rounded=False).convert("RGB")

    shrink(tile, 32).save(STATIC / "favicon.png")
    shrink(tile, 48).save(STATIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    shrink(fullbleed, 180).save(STATIC / "apple-touch-icon.png")
    shrink(fullbleed, 192).save(STATIC / "icon-192.png")
    shrink(fullbleed, 512).save(STATIC / "icon-512.png")
    print(f"wrote 5 icons to {STATIC}")


if __name__ == "__main__":
    main()
