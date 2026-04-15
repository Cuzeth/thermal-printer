"""Pretty-font renderer.

Takes the composer markup (same rules as features/text.py) and rasterizes it
to a single-column PIL image sized to the printer width, using real TTF fonts.
The printer then sends this as a raster graphic, sidestepping its blocky
built-in ROM font entirely.

Design decisions:
 - Body and list lines use Menlo (monospace) so receipts/tables keep column
   alignment using plain spaces.
 - Headings use Helvetica Bold with letter-spacing for a classy display look.
 - Horizontal rules are drawn as real pixels, not repeated dashes.
 - Everything is thresholded to 1-bit at 128 for crisp edges (dithering fuzzes
   type — we dither only photos, which live in features/image.py).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

import config


# ---------- font loading ----------

# Preferred fonts, tried in order. (path, ttc_index).
_FONT_CANDIDATES: dict[str, list[tuple[str, int]]] = {
    "sans_regular": [
        ("/System/Library/Fonts/Helvetica.ttc", 0),
        ("/System/Library/Fonts/HelveticaNeue.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ],
    "sans_bold": [
        ("/System/Library/Fonts/Helvetica.ttc", 1),
        ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    ],
    "mono_regular": [
        ("/System/Library/Fonts/Menlo.ttc", 0),
        ("/System/Library/Fonts/SFNSMono.ttf", 0),
        ("/System/Library/Fonts/Monaco.ttf", 0),
        ("/System/Library/Fonts/Supplemental/Courier New.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 0),
    ],
    "mono_bold": [
        ("/System/Library/Fonts/Menlo.ttc", 1),
        ("/System/Library/Fonts/Supplemental/Courier New Bold.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 0),
    ],
}

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    key = (kind, size)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    for path, idx in _FONT_CANDIDATES.get(kind, []):
        if not os.path.exists(path):
            continue
        try:
            f = ImageFont.truetype(path, size, index=idx)
            _font_cache[key] = f
            return f
        except Exception:
            continue
    # Last resort — bitmap default; looks bad but won't crash.
    f = ImageFont.load_default()
    _font_cache[key] = f
    return f


# ---------- sizes (in pixels) ----------
#
# Tuned for an 80mm printer (576px @ 203 dpi). At 24px, Menlo glyphs are
# ~13px wide -> ~44 cols of body text, which matches the 42-col widget
# layout nicely. If you ever switch to a 58mm printer (384px), drop these
# all by ~25% for matching proportions.

BODY = 24
HEADING = 54
SUBHEADING = 32
SMALL = 20

LINE_GAP = 4
BLOCK_GAP = 10


# ---------- inline markup ----------

INLINE_RE = re.compile(r"(\*\*.+?\*\*|__.+?__|~.+?~)")


@dataclass
class Span:
    text: str
    bold: bool = False
    underline: bool = False
    big: bool = False


def _parse_inline(line: str) -> list[Span]:
    spans: list[Span] = []
    for chunk in INLINE_RE.split(line):
        if not chunk:
            continue
        if chunk.startswith("**") and chunk.endswith("**"):
            spans.append(Span(chunk[2:-2], bold=True))
        elif chunk.startswith("__") and chunk.endswith("__"):
            spans.append(Span(chunk[2:-2], underline=True))
        elif chunk.startswith("~") and chunk.endswith("~") and len(chunk) > 2:
            spans.append(Span(chunk[1:-1], big=True))
        else:
            spans.append(Span(chunk))
    return spans


# ---------- renderer ----------

class Renderer:
    # Start modest and grow on demand. Most widget prints fit under 1 KiB tall;
    # the old fixed 16000-px canvas cost ~9 MB/request for nothing.
    _INITIAL_H = 1024
    _GROW_PAD = 512

    def __init__(self, width: Optional[int] = None):
        self.width = width or config.PRINTER_PIXEL_WIDTH
        self.pad = 8
        self.draw_w = self.width - 2 * self.pad
        self.canvas = Image.new("L", (self.width, self._INITIAL_H), 255)
        self.draw = ImageDraw.Draw(self.canvas)
        self.y = 8

    def _ensure_room(self, needed: int) -> None:
        """Grow the canvas if the next block won't fit. O(n) paste per grow,
        but bodies rarely grow more than once."""
        if self.y + needed <= self.canvas.height:
            return
        new_h = max(self.canvas.height * 2, self.y + needed + self._GROW_PAD)
        bigger = Image.new("L", (self.width, new_h), 255)
        bigger.paste(self.canvas, (0, 0))
        self.canvas = bigger
        self.draw = ImageDraw.Draw(self.canvas)

    def render(self, body: str) -> None:
        for raw in body.splitlines():
            self._emit(raw.rstrip())

    def finish(self) -> Image.Image:
        h = max(self.y + 8, 20)
        cropped = self.canvas.crop((0, 0, self.width, h))
        # Crisp threshold — best for text.
        return cropped.point(lambda v: 255 if v >= 128 else 0).convert("1")

    # ----- per-line dispatch -----

    def _emit(self, line: str) -> None:
        if not line:
            self._ensure_room(BODY // 2)
            self.y += BODY // 2
            return
        if line == "---":
            self._hr(double=False)
            return
        if line == "===":
            self._hr(double=True)
            return
        if line == "!!!":
            # Visual cut marker; the real paper-cut happens in the printer
            # layer which splits the body on this line.
            self._scissors()
            return
        if line.startswith("# "):
            self._heading(line[2:].strip(), HEADING, upper=True)
            return
        if line.startswith("## "):
            self._heading(line[3:].strip(), SUBHEADING, upper=False)
            return
        if line.startswith("> "):
            self._spans(_parse_inline(line[2:]), align="center")
            return
        if line.startswith("- "):
            self._bullet_line(line[2:])
            return
        if line.startswith("[ ] "):
            self._checkbox_line(line[4:], checked=False)
            return
        if line.lower().startswith("[x] "):
            self._checkbox_line(line[4:], checked=True)
            return
        self._spans(_parse_inline(line), align="left")

    # ----- blocks -----

    def _heading(self, text: str, size: int, upper: bool) -> None:
        if upper:
            text = text.upper()
        font = _font("sans_bold", size)
        tracking = max(1, size // 14)
        widths = [self.draw.textlength(ch, font=font) for ch in text]
        total = sum(widths) + tracking * max(0, len(text) - 1)
        if total > self.draw_w:
            # Too wide — drop tracking and/or shrink proportionally.
            scale = self.draw_w / total
            size = max(14, int(size * scale))
            font = _font("sans_bold", size)
            tracking = 1
            widths = [self.draw.textlength(ch, font=font) for ch in text]
            total = sum(widths) + tracking * max(0, len(text) - 1)
        self._ensure_room(size + BLOCK_GAP)
        x = self.pad + (self.draw_w - int(total)) // 2
        self.y += BLOCK_GAP // 2
        for i, ch in enumerate(text):
            self.draw.text((x, self.y), ch, font=font, fill=0)
            x += int(widths[i]) + tracking
        self.y += size + BLOCK_GAP

    def _bullet_line(self, text: str) -> None:
        bullet = "\u2022"
        font = _font("mono_bold", BODY)
        self.draw.text((self.pad + 6, self.y), bullet, font=font, fill=0)
        bw = int(self.draw.textlength(bullet + "  ", font=font))
        self._spans(_parse_inline(text), align="left", indent=6 + bw)

    def _checkbox_line(self, text: str, checked: bool) -> None:
        # Draw an actual box instead of a Unicode char — some mono fonts render
        # box glyphs poorly, and drawing is crisp at any size.
        size = BODY
        box_y = self.y + 3
        box_side = size - 6
        self.draw.rectangle(
            [(self.pad + 6, box_y), (self.pad + 6 + box_side, box_y + box_side)],
            outline=0,
            width=2,
        )
        if checked:
            # Bold checkmark strokes
            x0 = self.pad + 6
            y0 = box_y
            self.draw.line(
                [(x0 + 4, y0 + box_side // 2),
                 (x0 + box_side // 2, y0 + box_side - 4),
                 (x0 + box_side - 2, y0 + 3)],
                fill=0, width=3,
            )
        indent = 6 + box_side + 10
        self._spans(_parse_inline(text), align="left", indent=indent)

    def _hr(self, double: bool) -> None:
        self._ensure_room(24)
        self.y += 6
        y = self.y
        self.draw.line(
            [(self.pad, y), (self.width - self.pad, y)], fill=0, width=2
        )
        if double:
            self.draw.line(
                [(self.pad, y + 6), (self.width - self.pad, y + 6)],
                fill=0, width=2,
            )
            self.y += 6
        self.y += 10

    def _scissors(self) -> None:
        self._ensure_room(20)
        self.y += 4
        y = self.y
        x = self.pad
        dash = 10
        while x < self.width - self.pad:
            end = min(x + dash, self.width - self.pad)
            self.draw.line([(x, y), (end, y)], fill=0, width=1)
            x += dash * 2
        self.y += 12

    # ----- span layout -----

    def _spans(self, spans: list[Span], align: str, indent: int = 0) -> None:
        """Lay out inline spans with left/center alignment and word-wrap.

        Wrapping only applies to the longest span that's plain text; bold/big
        spans are drawn as given.
        """
        # Build a flat list of (token, span, font, size, width)
        pieces: list[tuple[str, Span, ImageFont.FreeTypeFont, int, int]] = []
        for sp in spans:
            size = BODY * 2 if sp.big else BODY
            font_kind = "mono_bold" if sp.bold else "mono_regular"
            font = _font(font_kind, size)
            # Split plain text spans on spaces for wrapping; keep bold/big as
            # single tokens.
            if sp.big or sp.bold or sp.underline:
                w = int(self.draw.textlength(sp.text, font=font))
                pieces.append((sp.text, sp, font, size, w))
            else:
                words = re.split(r"(\s+)", sp.text)
                for w in words:
                    if not w:
                        continue
                    pw = int(self.draw.textlength(w, font=font))
                    pieces.append((w, sp, font, size, pw))

        # Wrap into lines
        avail = self.draw_w - indent
        lines: list[list[tuple[str, Span, ImageFont.FreeTypeFont, int, int]]] = [[]]
        cur_w = 0
        for tok in pieces:
            _, _, _, _, w = tok
            if cur_w + w > avail and lines[-1]:
                lines.append([])
                cur_w = 0
                # Don't start a line with pure whitespace
                if tok[0].isspace():
                    continue
            lines[-1].append(tok)
            cur_w += w

        # Draw each line
        for line in lines:
            if not line:
                continue
            widths = [p[4] for p in line]
            total = sum(widths)
            max_size = max((p[3] for p in line), default=BODY)
            self._ensure_room(max_size + LINE_GAP)
            if align == "center":
                x = self.pad + (self.draw_w - total) // 2
            elif align == "right":
                x = self.pad + self.draw_w - total
            else:
                x = self.pad + indent
            for tok, sp, font, size, w in line:
                y_off = max_size - size  # baseline-ish align to bottom
                self.draw.text((x, self.y + y_off), tok, font=font, fill=0)
                if sp.underline:
                    uy = self.y + y_off + size - 2
                    self.draw.line([(x, uy), (x + w, uy)], fill=0, width=1)
                x += w
            self.y += max_size + LINE_GAP


# ---------- public API ----------

def split_cuts(body: str) -> list[str]:
    """Split body on lines containing exactly `!!!` — one print per segment."""
    segments: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if line.strip() == "!!!":
            segments.append("\n".join(current))
            current = []
        else:
            current.append(line)
    segments.append("\n".join(current))
    return [s for s in segments if s.strip()]


def render_markup(body: str, width: Optional[int] = None) -> Image.Image:
    r = Renderer(width)
    r.render(body)
    return r.finish()
