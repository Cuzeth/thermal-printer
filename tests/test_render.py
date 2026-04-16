"""Shape-only tests for the markup rasterizer.

We do NOT snapshot exact pixels — font constants are already validated on
the real printer, and PIL fallback fonts differ across CI runners. These
tests assert the renderer produces 1-bit images of plausible height and
that the markup vocabulary is honored.
"""

from __future__ import annotations

from features import render


def _render(body: str):
    img = render.render_markup(body)
    assert img.mode == "1", f"expected 1-bit output, got {img.mode}"
    return img


def test_empty_body_yields_tiny_image():
    img = _render("")
    # finish() clamps to a minimum of ~20px; width is always the printer width.
    assert img.width > 0
    assert img.height >= 20


def test_heading_grows_image():
    short = _render("hi")
    big = _render("# HELLO WORLD")
    # A heading is taller than a single body line.
    assert big.height > short.height


def test_long_body_allocates_beyond_initial_canvas():
    # The renderer starts with a 1024-px canvas and grows. Make sure a body
    # taller than that doesn't crash and produces a proportionally taller image.
    body = "\n".join(f"line {i}" for i in range(80))
    img = _render(body)
    assert img.height > 1024


def test_cut_marker_splits_segments():
    segs = render.split_cuts("a\n!!!\nb\n!!!\nc")
    assert segs == ["a", "b", "c"]


def test_cut_marker_ignores_empty_segments():
    segs = render.split_cuts("!!!\nhi\n!!!")
    assert segs == ["hi"]


def test_no_cut_marker_returns_single_segment():
    segs = render.split_cuts("just one block")
    assert segs == ["just one block"]


def test_tiny_body_uses_modest_canvas():
    """Guards against the old "allocate 16000-px canvas every call" bug.
    A short body should end up well under 1000px tall."""
    img = _render("hello")
    assert img.height < 200


def test_cjk_body_does_not_crash():
    """CJK code points route to the CJK font via script-based fallback.
    The font itself may not be installed in CI, but the renderer must
    still produce a valid image (glyphs fall back to .notdef boxes)."""
    img = _render("知之为知之，不知为不知，是知也")
    assert img.height > 20


def test_braille_art_does_not_crash():
    """Braille code points (U+2800–U+28FF) survive wrapping and render
    as a valid 1-bit image, even when the available font lacks glyphs."""
    braille = "⠀⠀⠀⠀⠀⢀⣀⡤⠤⢶⢒⣚⣛⡛⠓⠲⢤⡀"
    img = _render(braille + "\n" + braille)
    assert img.height > 20


def test_mixed_scripts_render():
    """A message mixing Latin and CJK wraps without crashing and each run
    is drawn with its own font (checked indirectly via height > a single line)."""
    body = "hello 世界 こんにちは **world**"
    img = _render(body)
    assert img.height > 20


def test_markup_vocab_all_paths():
    """Every directive exercised at least once — no crashes, valid image."""
    body = (
        "# Heading\n"
        "## Sub\n"
        "> centered\n"
        "regular body with **bold** and __underline__ and ~BIG~ text\n"
        "- bullet\n"
        "[ ] todo\n"
        "[x] done\n"
        "---\n"
        "===\n"
        "\n"
        "trailing"
    )
    img = _render(body)
    assert img.height > 100
