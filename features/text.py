"""Rich text printing — supports a small markup language in the composer.

Markup is line-based. A line can start with an optional directive:

    # Heading       -> big bold centered (double width/height)
    ## Subheading   -> bold centered
    > Centered      -> centered normal
    - item          -> bullet
    [ ] todo        -> unchecked checkbox
    [x] done        -> checked checkbox
    ---             -> horizontal rule
    ===             -> double horizontal rule
    !!!             -> cut paper here (split into multiple receipts)

Inline: **bold**, __underline__, ~big~text~ (double width/height chunk).
Everything else prints as left-aligned body text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import config
from printer import hr, heading


@dataclass
class Segment:
    text: str
    bold: bool = False
    underline: bool = False
    big: bool = False


_INLINE_PATTERN = re.compile(
    r"(\*\*.+?\*\*|__.+?__|~.+?~)",
)


def _parse_inline(line: str) -> list[Segment]:
    segments: list[Segment] = []
    for chunk in _INLINE_PATTERN.split(line):
        if not chunk:
            continue
        if chunk.startswith("**") and chunk.endswith("**"):
            segments.append(Segment(chunk[2:-2], bold=True))
        elif chunk.startswith("__") and chunk.endswith("__"):
            segments.append(Segment(chunk[2:-2], underline=True))
        elif chunk.startswith("~") and chunk.endswith("~") and len(chunk) > 2:
            segments.append(Segment(chunk[1:-1], big=True))
        else:
            segments.append(Segment(chunk))
    return segments


def _emit(p, segments: list[Segment]) -> None:
    for seg in segments:
        p.set(
            align="left",
            bold=seg.bold,
            underline=1 if seg.underline else 0,
            double_height=seg.big,
            double_width=seg.big,
        )
        p.text(seg.text)
    p.set(
        align="left",
        bold=False,
        underline=0,
        double_height=False,
        double_width=False,
    )
    p.text("\n")


def render(p, body: str, align: str = "left") -> None:
    """Render markup body on the printer. Does not cut; caller handles that."""
    for raw in body.splitlines():
        line = raw.rstrip()

        if not line:
            p.text("\n")
            continue

        if line == "---":
            hr(p, "-")
            continue
        if line == "===":
            hr(p, "=")
            continue
        if line == "!!!":
            p.cut()
            continue

        if line.startswith("# "):
            heading(p, line[2:].strip())
            continue
        if line.startswith("## "):
            p.set(align="center", bold=True)
            p.text(line[3:].strip() + "\n")
            p.set(align="left", bold=False)
            continue
        if line.startswith("> "):
            p.set(align="center")
            _emit(p, _parse_inline(line[2:]))
            p.set(align="left")
            continue
        if line.startswith("- "):
            p.text("  \u2022 ")
            _emit(p, _parse_inline(line[2:]))
            continue
        if line.startswith("[ ] "):
            p.text("  [ ] ")
            _emit(p, _parse_inline(line[4:]))
            continue
        if line.lower().startswith("[x] "):
            p.text("  [X] ")
            _emit(p, _parse_inline(line[4:]))
            continue

        p.set(align=align)
        _emit(p, _parse_inline(line))
        p.set(align="left")


def preview(body: str) -> str:
    """Render a plain-text monospace preview roughly matching what will print."""
    width = config.RECEIPT_WIDTH
    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line:
            lines.append("")
            continue
        if line == "---":
            lines.append("-" * width)
            continue
        if line == "===":
            lines.append("=" * width)
            continue
        if line == "!!!":
            lines.append("\u2702" + "\u2500" * (width - 1))
            continue
        if line.startswith("# "):
            txt = line[2:].strip().upper()
            lines.append(txt.center(width))
            continue
        if line.startswith("## "):
            lines.append(line[3:].strip().center(width))
            continue
        if line.startswith("> "):
            lines.append(line[2:].center(width))
            continue
        if line.startswith("- "):
            lines.append("  \u2022 " + _strip_inline(line[2:]))
            continue
        if line.startswith("[ ] "):
            lines.append("  \u2610 " + _strip_inline(line[4:]))
            continue
        if line.lower().startswith("[x] "):
            lines.append("  \u2611 " + _strip_inline(line[4:]))
            continue
        lines.append(_strip_inline(line))
    return "\n".join(lines)


def _strip_inline(line: str) -> str:
    line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
    line = re.sub(r"__(.+?)__", r"\1", line)
    line = re.sub(r"~(.+?)~", r"\1", line)
    return line
