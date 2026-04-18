"""Widget content generators. Deterministic ones get exact checks; randomized
ones get shape checks."""

from __future__ import annotations

import random

from features import widgets


def test_dice_rolls_in_range():
    random.seed(0)
    out = widgets.roll_dice(count=5, sides=6)
    assert "5d6" in out
    # Total line ends the block.
    total_line = [ln for ln in out.splitlines() if ln.startswith("# ") and ln != "# DICE ROLL"]
    assert total_line, "dice output should include a total line"


def test_dice_clamps_input():
    # count capped at 20, sides at 100 per roll_dice()
    out = widgets.roll_dice(count=9999, sides=9999)
    assert "20d100" in out


def test_todo_renders_checkboxes():
    out = widgets.todo("today", ["buy milk", "", "water plants"])
    # Empty item is dropped.
    assert "[ ] buy milk" in out
    assert "[ ] water plants" in out
    assert "[ ] \n" not in out


def test_receipt_totals_add_up():
    out = widgets.receipt(
        store="TEST",
        items=[
            {"name": "apple", "qty": 2, "price": 1.50},
            {"name": "bread", "qty": 1, "price": 3.00},
        ],
        tax_rate=10.0,
    )
    # subtotal = 6.00, tax = 0.60, total = 6.60
    assert "$6.00" in out
    assert "$0.60" in out
    assert "$6.60" in out


def test_ascii_art_known_name():
    out = widgets.ascii_art("cat")
    assert "CAT" in out


def test_ascii_art_unknown_falls_back():
    # Unknown names still return *something*; doesn't raise.
    out = widgets.ascii_art("nonexistent")
    assert out.startswith("## NONEXISTENT")


def test_friend_message_quotes_username():
    out = widgets.friend_message("alice", "hello")
    assert "## from alice" in out
    assert "hello" in out


def test_friend_message_handles_empty_username():
    out = widgets.friend_message("", "hi")
    assert "## from anon" in out
