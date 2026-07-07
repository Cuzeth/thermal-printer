"""Scheduled-briefing schedule math: BRIEFING_SCHEDULE parsing fails loudly
on typos, and the next-occurrence countdown wraps past midnight correctly."""

from __future__ import annotations

from datetime import datetime, time as dt_time

import pytest

import app as app_module


def test_parse_schedule_empty_is_none():
    """Unset env var means the feature is off — no thread, no error."""
    assert app_module._parse_schedule("") is None


def test_parse_schedule_accepts_hhmm():
    """Both zero-padded and bare-hour forms are valid 24h times."""
    assert app_module._parse_schedule("07:30") == dt_time(7, 30)
    assert app_module._parse_schedule("7:30") == dt_time(7, 30)


def test_parse_schedule_rejects_garbage():
    """A typo'd schedule must raise at boot and name the env var, so the
    owner learns about it immediately instead of missing briefings."""
    for value in ("0730", "25:00", "seven", "07:30:00"):
        with pytest.raises(ValueError, match="BRIEFING_SCHEDULE"):
            app_module._parse_schedule(value)


def test_seconds_until_wraps_to_tomorrow():
    """A target already past today counts down to tomorrow's occurrence;
    one still ahead counts down to today's."""
    now = datetime(2026, 7, 6, 8, 0)
    assert app_module._seconds_until(dt_time(7, 30), now) == 23.5 * 3600
    assert app_module._seconds_until(dt_time(8, 30), now) == 1800
