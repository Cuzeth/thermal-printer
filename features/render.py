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
   each text run by Unicode script (latin / CJK / arabic / braille / symbols
   / hieroglyph) and draw each sub-run with whichever font actually has
   glyphs for it. Requires `fonts-noto-cjk` on the Pi for CJK, and
   `fonts-noto-core` for Arabic + broad symbol coverage; the full
   `fonts-dejavu` (not `-core`) carries the braille patterns.
 - RTL scripts (Arabic): PIL/FreeType draws characters in the order
   they appear in the string, left-to-right. Without shaping that means an
   Arabic sentence prints with its letters mirrored across the word — the
   text reads backwards. We pre-process RTL runs with `arabic-reshaper`
   (to pick the initial/medial/final glyph forms) and `python-bidi` (UAX #9
   reorder to visual order) before any measurement or drawing. If those
   packages aren't installed the renderer still works — Arabic just prints
   in logical order, same as before.
"""

from __future__ import annotations

import os
import re
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

import config
from features.markup import Span, parse_inline as _parse_inline


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
    # Arabic (as the best Mac coverage is Arial Unicode which
    # is listed among the Arabic candidates). Used together with BiDi +
    # reshaping in `_shape_bidi` so letters join and words read right-to-left.
    "arabic_regular": [
        ("/System/Library/Fonts/SFArabic.ttf", 0),
        ("/System/Library/Fonts/GeezaPro.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
        ("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ],
    "arabic_bold": [
        ("/System/Library/Fonts/SFArabic.ttf", 0),
        ("/System/Library/Fonts/GeezaPro.ttc", 1),
        ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
        ("/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    ],
    # Braille Patterns (U+2800–U+28FF). Most UI fonts skip this block, so
    # without an explicit route braille art prints as rows of .notdef boxes.
    # Apple Symbols renders the dots as crisp filled circles; DejaVu Sans
    # does solid rectangles, both legible on a thermal printer.
    "braille": [
        ("/System/Library/Fonts/Apple Symbols.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
        ("/System/Library/Fonts/Apple Braille.ttf", 0),
    ],
    # Catch-all for "miscellaneous Unicode": music symbols, math alphanumerics,
    # dingbats, misc technical, emoji-ish glyphs. Apple Symbols on macOS has
    # surprisingly broad coverage including the astral-plane symbol blocks.
    "symbols": [
        ("/System/Library/Fonts/Apple Symbols.ttf", 0),
        ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
        ("/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf", 0),
        ("/usr/share/fonts/truetype/noto/NotoSansSymbols-Regular.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ],
    # Egyptian Hieroglyphs (U+13000–U+1342F). Separate font on every platform.
    "hieroglyph": [
        ("/System/Library/Fonts/Supplemental/NotoSansEgyptianHieroglyphs-Regular.ttf", 0),
        ("/usr/share/fonts/truetype/noto/NotoSansEgyptianHieroglyphs-Regular.ttf", 0),
    ],
    # Friends can pick a display font for their name header. macOS ships
    # several display faces out of the box; Linux candidates fall back to
    # DejaVu + Liberation, which are present on most distros.
    "serif_bold": [
        ("/System/Library/Fonts/NewYork.ttf", 0),
        ("/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf", 0),
        ("/System/Library/Fonts/Times.ttc", 1),
        ("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 0),
    ],
    "script_regular": [
        ("/System/Library/Fonts/SnellRoundhand.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Brush Script.ttf", 0),
        ("/System/Library/Fonts/Supplemental/Zapfino.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf", 0),
    ],
    "gothic_regular": [
        # macOS doesn't ship a true blackletter; Papyrus is the closest
        # built-in "weird old" feel. Chalkduster and Herculanum make decent
        # alt fall-backs (inscribed caps / hand-chalk).
        ("/System/Library/Fonts/Supplemental/Papyrus.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Chalkduster.ttf", 0),
        ("/System/Library/Fonts/Supplemental/Herculanum.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", 0),
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
    # Hebrew (routed with Arabic — Arial Unicode covers both on Mac, Noto on Pi).
    if 0x0590 <= c <= 0x05FF:
        return "arabic"
    # Arabic: main block, supplement, extended-A, presentation forms A/B.
    # After reshaping, characters land in the presentation-form ranges, so
    # those must route the same way.
    if (0x0600 <= c <= 0x06FF or 0x0750 <= c <= 0x077F or
            0x08A0 <= c <= 0x08FF or 0xFB50 <= c <= 0xFDFF or
            0xFE70 <= c <= 0xFEFF):
        return "arabic"
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
    # Braille Patterns — dedicated block, very few fonts cover it.
    if 0x2800 <= c <= 0x28FF:
        return "braille"
    # Egyptian Hieroglyphs (SMP)
    if 0x13000 <= c <= 0x1342F:
        return "hieroglyph"
    # Miscellaneous SMP symbol blocks: Byzantine/Western Musical Symbols,
    # Math Alphanumeric Symbols, Misc Symbols and Pictographs, etc.
    # Menlo/Helvetica don't reach into the astral plane, so anything up
    # there that isn't CJK/hieroglyph goes to the "symbols" font.
    if 0x10000 <= c <= 0x1FFFF:
        return "symbols"
    return "latin"


def _font_for(base_kind: str, script: str, size: int) -> ImageFont.FreeTypeFont:
    """Return a font that actually has glyphs for this script.

    Scripts that have dedicated font candidates (CJK, Arabic, braille,
    symbols, hieroglyph) try those first; if the host doesn't have any
    of them (e.g. CI without `fonts-noto-cjk`), we fall back to the base
    font so the char at least renders as .notdef instead of crashing.
    """
    route: Optional[str] = None
    if script == "cjk":
        route = "cjk_bold" if base_kind.endswith("_bold") else "cjk_regular"
    elif script == "arabic":
        route = "arabic_bold" if base_kind.endswith("_bold") else "arabic_regular"
    elif script == "braille":
        route = "braille"
    elif script == "symbols":
        route = "symbols"
    elif script == "hieroglyph":
        route = "hieroglyph"
    if route:
        f = _font_try(route, size)
        if f is not None:
            return f
    return _font(base_kind, size)


# ---------- RTL shaping + bidi reorder ----------

_bidi_loaded = False
_reshape_fn = None
_bidi_fn = None


def _load_bidi() -> None:
    """Lazy-load arabic-reshaper and python-bidi. Both optional — if
    either import fails we silently degrade (letters stay in logical order,
    which is how the renderer behaved before these libraries arrived)."""
    global _bidi_loaded, _reshape_fn, _bidi_fn
    if _bidi_loaded:
        return
    _bidi_loaded = True
    try:
        import arabic_reshaper  # type: ignore
        _reshape_fn = arabic_reshaper.reshape
    except Exception:
        _reshape_fn = None
    try:
        try:
            from bidi import get_display  # python-bidi ≥ 0.5
        except ImportError:
            from bidi.algorithm import get_display  # older python-bidi
        _bidi_fn = get_display
    except Exception:
        _bidi_fn = None


def _has_rtl(text: str) -> bool:
    for ch in text:
        c = ord(ch)
        if (0x0590 <= c <= 0x05FF or 0x0600 <= c <= 0x06FF or
                0x0750 <= c <= 0x077F or 0x08A0 <= c <= 0x08FF or
                0xFB50 <= c <= 0xFDFF or 0xFE70 <= c <= 0xFEFF):
            return True
    return False


def _shape_bidi(text: str) -> str:
    """Shape + reorder any Arabic text into visual order.

    Returns `text` unchanged when there are no RTL characters, so the fast
    path for normal Latin/CJK content pays nothing.
    """
    if not text or not _has_rtl(text):
        return text
    _load_bidi()
    if _reshape_fn is not None:
        text = _reshape_fn(text)
    if _bidi_fn is not None:
        text = _bidi_fn(text)
    return text


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
#
# Inline parsing (Span + _parse_inline) is shared with features/text.py
# via features/markup.py.

# Styled subheading markup: `:style: text` on its own line renders the text
# as a centered subheading in the picked display font. Used by friends'
# name-style preference on the friends page.
STYLE_LINE_RE = re.compile(r"^:([a-z]+):\s*(.*)$")

# `style key` -> (font kind, size, transform). Keep the set small: adding a
# style means shipping a font candidate for it in _FONT_CANDIDATES.
NAME_STYLES: dict[str, tuple[str, int, str]] = {
    "serif":  ("serif_bold",     40, "none"),
    "script": ("script_regular", 48, "none"),
    "gothic": ("gothic_regular", 40, "none"),
    "mono":   ("mono_bold",      34, "none"),
    "caps":   ("sans_bold",      40, "upper"),
}


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
        # `:style: text` — styled subheading (script/serif/mono/gothic/caps).
        # Checked before the regular heading paths so the `:` prefix wins.
        m = STYLE_LINE_RE.match(line)
        if m and m.group(1) in NAME_STYLES:
            self._styled_heading(_shape_bidi(m.group(2).strip()), m.group(1))
            return
        # For text-bearing branches we BiDi-shape the *content* (without the
        # markup prefix) so shape_bidi doesn't have to reason about `# `, `- `
        # and friends when deciding the paragraph's base direction.
        if line.startswith("# "):
            self._heading(_shape_bidi(line[2:].strip()), HEADING, upper=True)
            return
        if line.startswith("## "):
            self._heading(_shape_bidi(line[3:].strip()), SUBHEADING, upper=False)
            return
        if line.startswith("> "):
            self._spans(_parse_inline(_shape_bidi(line[2:])), align="center")
            return
        if line.startswith("- "):
            self._bullet_line(_shape_bidi(line[2:]))
            return
        if line.startswith("[ ] "):
            self._checkbox_line(_shape_bidi(line[4:]), checked=False)
            return
        if line.lower().startswith("[x] "):
            self._checkbox_line(_shape_bidi(line[4:]), checked=True)
            return
        self._spans(_parse_inline(_shape_bidi(line)), align="left")

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

    def _styled_heading(self, text: str, style: str) -> None:
        """Centered subheading rendered in a display font.

        Used by friends' `:style: from Alice` name-header markup. We fall
        back to the font chain's last resort silently if the chosen display
        face isn't installed on this machine — the text still prints, just
        in the default sans face.
        """
        base_kind, size, transform = NAME_STYLES[style]
        if transform == "upper":
            text = text.upper()
        total = self._measure_text(text, base_kind, size)
        if total > self.draw_w:
            scale = self.draw_w / total
            size = max(14, int(size * scale))
            total = self._measure_text(text, base_kind, size)
        self._ensure_room(size + BLOCK_GAP)
        x = self.pad + (self.draw_w - total) // 2
        self.y += BLOCK_GAP // 2
        self._draw_text(x, self.y, text, base_kind, size)
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

        Every span is tokenized for wrapping — styled runs included. Styled
        spans used to stay single tokens, so a long ~big~ run rendered wider
        than the canvas and was clipped off the right edge. Each token
        carries a base font `kind` rather than a resolved font so the
        drawing step can do per-character CJK fallback without losing
        width accuracy.
        """
        # (token, span, base_kind, size, width)
        Piece = tuple[str, Span, str, int, int]
        pieces: list[Piece] = []
        for sp in spans:
            size = BODY * 2 if sp.big else BODY
            base_kind = "mono_bold" if sp.bold else "mono_regular"
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
