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
 - Per-character font fallback: PIL has no built-in fallback, so we split
   each text run by Unicode script (CJK / everything-else) and draw each
   sub-run with whichever font actually has glyphs for it. Requires
   `fonts-noto-cjk` on the Pi for Chinese/Japanese/Korean, and the full
   `fonts-dejavu` package (not `-core`) for braille and other wide Unicode.
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
    # CJK coverage (Chinese, Japanese, Korean). Used via script-based fallback
    # when a message contains CJK code points. `fonts-noto-cjk` on Pi / Debian
    # ships the .ttc at these paths. On macOS we use the system CJK fonts.
    "cjk_regular": [
        ("/System/Library/Fonts/PingFang.ttc", 0),
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf", 0),
    ],
    "cjk_bold": [
        ("/System/Library/Fonts/PingFang.ttc", 3),
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", 1),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.otf", 0),
    ],
}

_font_cache: dict[tuple[str, int], Optional[ImageFont.FreeTypeFont]] = {}

# Sentinel so a failed lookup isn't retried every call.
_NO_FONT = object()


def _font_try(kind: str, size: int) -> Optional[ImageFont.FreeTypeFont]:
    """Try to load a font. Returns None if no candidate is available.

    Cached — repeated lookups are O(1) after the first miss.
    """
    key = (kind, size)
    cached = _font_cache.get(key, _NO_FONT)
    if cached is not _NO_FONT:
        return cached  # type: ignore[return-value]
    for path, idx in _FONT_CANDIDATES.get(kind, []):
        if not os.path.exists(path):
            continue
        try:
            f = ImageFont.truetype(path, size, index=idx)
            _font_cache[key] = f
            return f
        except Exception:
            continue
    _font_cache[key] = None
    return None


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    f = _font_try(kind, size)
    if f is not None:
        return f
    # Last resort — bitmap default; looks bad but won't crash.
    return ImageFont.load_default()


# ---------- script detection + font fallback ----------

def _script(ch: str) -> str:
    """Classify a character for font routing. Cheap and allocation-free."""
    if not ch:
        return "latin"
    c = ord(ch)
    # Hiragana / Katakana / Katakana-Phonetic
    if 0x3040 <= c <= 0x30FF or 0x31F0 <= c <= 0x31FF:
        return "cjk"
    # CJK Symbols and Punctuation
    if 0x3000 <= c <= 0x303F:
        return "cjk"
    # CJK Unified Ideographs + Extension A
    if 0x3400 <= c <= 0x9FFF:
        return "cjk"
    # Hangul Syllables / Jamo
    if 0xAC00 <= c <= 0xD7AF or 0x1100 <= c <= 0x11FF:
        return "cjk"
    # Halfwidth/Fullwidth forms (often mixed with CJK)
    if 0xFF00 <= c <= 0xFFEF:
        return "cjk"
    # CJK Extensions B–F (supplementary plane)
    if 0x20000 <= c <= 0x2FA1F:
        return "cjk"
    return "latin"


def _font_for(base_kind: str, script: str, size: int) -> ImageFont.FreeTypeFont:
    """Return a font that actually has glyphs for this script.

    For CJK we route to the CJK candidates; if they're missing on this host
    (e.g. local dev without `fonts-noto-cjk`), we fall back to the base font
    so the char at least renders as .notdef instead of crashing.
    """
    if script == "cjk":
        cjk_kind = "cjk_bold" if base_kind.endswith("_bold") else "cjk_regular"
        f = _font_try(cjk_kind, size)
        if f is not None:
            return f
    return _font(base_kind, size)


def _wrap_tokens(text: str) -> list[str]:
    """Split `text` into wrap-friendly tokens.

    Latin runs are split on whitespace (keeps spaces as their own tokens so
    word-wrap behaves). CJK runs are split one character per token — CJK
    text has no word spaces, so breaking between any two chars is the
    normal way to wrap it.
    """
    if not text:
        return []
    out: list[str] = []
    for chunk in re.split(r"(\s+)", text):
        if not chunk:
            continue
        # Is this a CJK-heavy chunk? If yes, emit one char per token.
        if any(_script(ch) == "cjk" for ch in chunk):
            out.extend(chunk)
        else:
            out.append(chunk)
    return out


def _split_by_script(text: str) -> list[tuple[str, str]]:
    """Break `text` into consecutive runs of same-script characters."""
    if not text:
        return []
    runs: list[tuple[str, str]] = []
    cur = _script(text[0])
    buf = [text[0]]
    for ch in text[1:]:
        s = _script(ch)
        if s == cur:
            buf.append(ch)
        else:
            runs.append(("".join(buf), cur))
            buf = [ch]
            cur = s
    runs.append(("".join(buf), cur))
    return runs


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

    # ----- font-fallback drawing primitives -----
    #
    # PIL's ImageFont has no automatic fallback chain, so we split each piece
    # of text into script-homogeneous runs and pick a covering font per run.
    # Widths are measured with the run's actual font so wrapping stays honest.

    def _measure_text(self, text: str, base_kind: str, size: int) -> int:
        total = 0
        for run, script in _split_by_script(text):
            font = _font_for(base_kind, script, size)
            total += int(self.draw.textlength(run, font=font))
        return total

    def _draw_text(self, x: int, y: int, text: str, base_kind: str, size: int) -> int:
        """Draw text with per-run font fallback. Returns total width drawn."""
        dx = 0
        for run, script in _split_by_script(text):
            font = _font_for(base_kind, script, size)
            self.draw.text((x + dx, y), run, font=font, fill=0)
            dx += int(self.draw.textlength(run, font=font))
        return dx

    def render(self, body: str) -> None:
        in_pre = False
        for raw in body.splitlines():
            if raw.strip() == "```":
                in_pre = not in_pre
                continue
            if in_pre:
                self._pre_line(raw)
                continue
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
            # .upper() is a no-op on CJK chars, which is what we want.
            text = text.upper()
        base_kind = "sans_bold"

        def measure(size_: int, tracking_: int):
            fonts = [_font_for(base_kind, _script(ch), size_) for ch in text]
            widths_ = [int(self.draw.textlength(ch, font=f)) for ch, f in zip(text, fonts)]
            total_ = sum(widths_) + tracking_ * max(0, len(text) - 1)
            return fonts, widths_, total_

        tracking = max(1, size // 14)
        fonts, widths, total = measure(size, tracking)
        if total > self.draw_w:
            # Too wide — drop tracking and shrink proportionally.
            scale = self.draw_w / total
            size = max(14, int(size * scale))
            tracking = 1
            fonts, widths, total = measure(size, tracking)
        self._ensure_room(size + BLOCK_GAP)
        x = self.pad + (self.draw_w - int(total)) // 2
        self.y += BLOCK_GAP // 2
        for i, ch in enumerate(text):
            self.draw.text((x, self.y), ch, font=fonts[i], fill=0)
            x += widths[i] + tracking
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

    def _pre_line(self, line: str) -> None:
        self._ensure_room(BODY + LINE_GAP)
        self._draw_text(self.pad, self.y, line, "mono_regular", BODY)
        self.y += BODY + LINE_GAP

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

        Wrapping applies to plain-text runs; bold/big spans stay as single
        tokens. Each token carries a base font `kind` rather than a resolved
        font so the drawing step can do per-character CJK fallback without
        losing width accuracy.
        """
        # (token, span, base_kind, size, width)
        Piece = tuple[str, Span, str, int, int]
        pieces: list[Piece] = []
        for sp in spans:
            size = BODY * 2 if sp.big else BODY
            base_kind = "mono_bold" if sp.bold else "mono_regular"
            if sp.big or sp.bold or sp.underline:
                w = self._measure_text(sp.text, base_kind, size)
                pieces.append((sp.text, sp, base_kind, size, w))
            else:
                # Split on runs of ASCII whitespace so wrapping can break. CJK
                # chars have no spaces between them, so we also split each CJK
                # run into per-character tokens so wrapping happens mid-word
                # (which is how CJK text is normally wrapped).
                for tok in _wrap_tokens(sp.text):
                    pw = self._measure_text(tok, base_kind, size)
                    pieces.append((tok, sp, base_kind, size, pw))

        # Wrap into lines
        avail = self.draw_w - indent
        lines: list[list[Piece]] = [[]]
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
            for tok, sp, base_kind, size, w in line:
                y_off = max_size - size  # baseline-ish align to bottom
                self._draw_text(x, self.y + y_off, tok, base_kind, size)
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
