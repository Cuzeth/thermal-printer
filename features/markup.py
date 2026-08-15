"""Shared inline-markup grammar for the composer language.

Two renderers consume the same vocabulary — features/text.py (printer ROM
font) and features/render.py (PIL rasterizer) — and each used to carry its
own copy of the span parser. The grammar lives here once so a new inline
style is a one-file change.

Inline forms: **bold**, *italic*, __underline__, ~big~ (double width/height
chunk). Line-level directives (headings, rules, cuts, bullets) stay in the
renderers: they differ per backend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# `**` must come before `*` in the alternation so bold wins at the same
# position; the parse branches below repeat that ordering.
INLINE_RE = re.compile(r"(\*\*.+?\*\*|__.+?__|~.+?~|\*.+?\*)")


@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    big: bool = False


def parse_inline(line: str) -> list[Span]:
    spans: list[Span] = []
    for chunk in INLINE_RE.split(line):
        if not chunk:
            continue
        if chunk.startswith("**") and chunk.endswith("**"):
            spans.append(Span(chunk[2:-2], bold=True))
        elif chunk.startswith("__") and chunk.endswith("__"):
            spans.append(Span(chunk[2:-2], underline=True))
        elif chunk.startswith("*") and chunk.endswith("*") and len(chunk) > 2:
            spans.append(Span(chunk[1:-1], italic=True))
        elif chunk.startswith("~") and chunk.endswith("~") and len(chunk) > 2:
            spans.append(Span(chunk[1:-1], big=True))
        else:
            spans.append(Span(chunk))
    return spans


def strip_inline(line: str) -> str:
    """Remove inline markers, keeping the text — for plain previews."""
    line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
    line = re.sub(r"\*(.+?)\*", r"\1", line)
    line = re.sub(r"__(.+?)__", r"\1", line)
    line = re.sub(r"~(.+?)~", r"\1", line)
    return line
