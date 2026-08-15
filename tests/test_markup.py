"""Contract tests for the shared inline-markup grammar (features/markup.py).

Both renderers consume parse_inline(), so the grammar's edge cases are
pinned here once instead of per-backend.
"""

from __future__ import annotations

from features.markup import parse_inline, strip_inline


def test_italic_parses_to_italic_span():
    """`*text*` becomes one italic span with the markers removed."""
    spans = parse_inline("*slanted*")
    assert len(spans) == 1
    assert spans[0].text == "slanted"
    assert spans[0].italic
    assert not (spans[0].bold or spans[0].underline or spans[0].big)


def test_bold_wins_over_italic():
    """`**text**` must stay bold — the single-star italic form cannot
    swallow the double-star bold form at the same position."""
    spans = parse_inline("**loud**")
    assert len(spans) == 1
    assert spans[0].bold and not spans[0].italic
    assert spans[0].text == "loud"


def test_mixed_inline_forms_share_a_line():
    """Bold, italic, underline, and big coexist on one line, each carrying
    only its own flag."""
    spans = parse_inline("a **b** *c* __d__ ~e~")
    flags = [(s.text, s.bold, s.italic, s.underline, s.big) for s in spans]
    assert ("b", True, False, False, False) in flags
    assert ("c", False, True, False, False) in flags
    assert ("d", False, False, True, False) in flags
    assert ("e", False, False, False, True) in flags


def test_lone_asterisk_stays_plain():
    """A single `*` (footnote marker, shopping-list star) is not markup."""
    spans = parse_inline("milk * eggs")
    assert len(spans) == 1
    assert spans[0].text == "milk * eggs"
    assert not spans[0].italic


def test_strip_inline_removes_italic_markers():
    """Plain previews drop the stars but keep the text — and must strip
    `**` before `*` so bold markers don't leave orphan stars behind."""
    assert strip_inline("a **b** *c* __d__ ~e~") == "a b c d e"
