"""UTC capsule dates and owner-local quiet hours; no printer or database I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MAX_DAYS = 365
PER_USER_CAP = 10
TOTAL_CAP = 200
POLL_SECONDS = 15


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(when: datetime) -> str:
    """One fixed-width UTC representation sorts correctly in SQLite."""
    return when.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_requested(value, now: datetime) -> datetime | None:
    """An omitted date means now; an explicit date must identify an instant."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value,
    ):
        raise ValueError("choose a delivery date and time with a timezone")
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("invalid delivery date and time") from None
    if when <= now:
        raise ValueError("delivery must be in the future")
    if when > now + timedelta(days=MAX_DAYS):
        raise ValueError(f"choose a date within {MAX_DAYS} days")
    return when


@dataclass(frozen=True)
class QuietHours:
    start: time
    end: time
    zone: ZoneInfo

    def contains(self, when: datetime) -> bool:
        local = when.astimezone(self.zone).time()
        if self.start < self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end

    def release(self, when: datetime) -> datetime:
        """Return the first allowed instant, following actual UTC minutes.

        Advancing UTC rather than constructing a local end time avoids imaginary
        spring-forward times. At fall-back, each repeated minute follows the same
        wall-clock rule. The worker rechecks in case a queue spans a boundary.
        """
        when = when.astimezone(timezone.utc)
        if not self.contains(when):
            return when
        candidate = when.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(48 * 60):
            if not self.contains(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise ValueError("quiet hours have no opening in the next 48 hours")


def parse_quiet(start: str, end: str, zone: str) -> QuietHours | None:
    if not start and not end:
        return None
    try:
        if not all(re.fullmatch(r"\d{2}:\d{2}", value) for value in (start, end)):
            raise ValueError
        first, last = time.fromisoformat(start), time.fromisoformat(end)
        if first == last:
            raise ValueError
        return QuietHours(first, last, ZoneInfo(zone))
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("FRIEND_QUIET_START/END need different HH:MM times and "
                         "FRIEND_QUIET_TIMEZONE must name an IANA timezone") from None
