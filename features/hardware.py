"""Hardware-level printer controls.

All commands here bypass the text/image layer and speak raw ESC/POS bytes
to drive hardware features: cash drawer, buzzer, paper cut, manual feed,
print density, code page, reset, self-test, and status queries.

Commands are written for a generic 80mm ESC/POS printer. Support varies
by model — the buzzer, cash-drawer, and density commands in particular
may silently no-op on some units.
"""

from __future__ import annotations

import re
from typing import Optional


# ---------- low-level helper ----------

def send_bytes(p, data: bytes) -> None:
    """Write raw bytes to the printer, using whatever low-level escape hatch
    the python-escpos backend gives us. Dummy() uses `_raw`, Usb() too.

    Public on purpose — the raw-console route and led.py send pre-parsed
    byte strings through here."""
    if hasattr(p, "_raw"):
        p._raw(data)
    else:
        # Last-ditch: write through the attached device (Usb uses `device`).
        p.device.write(p.out_ep, data)  # type: ignore[attr-defined]


# ---------- cash drawer ----------

def cash_drawer(p, pin: int = 2, on_time: int = 50, off_time: int = 200) -> None:
    """Kick the cash drawer on pin 2 or pin 5.
    on_time/off_time are in units of 2ms (ESC/POS convention)."""
    m = 0 if pin == 2 else 1
    on_time = max(1, min(255, int(on_time)))
    off_time = max(1, min(255, int(off_time)))
    send_bytes(p, bytes([0x1B, 0x70, m, on_time, off_time]))


# ---------- buzzer ----------

def beep(p, count: int = 1, duration_units: int = 3) -> None:
    """Beep the buzzer `count` times, each `duration_units*100ms`.
    Requires a printer with a buzzer. Common command: ESC B n t."""
    n = max(1, min(9, int(count)))
    t = max(1, min(9, int(duration_units)))
    send_bytes(p, bytes([0x1B, 0x42, n, t]))


# ---------- feed ----------

def feed_lines(p, n: int) -> None:
    """Feed `n` lines at the current line height."""
    n = max(0, min(255, int(n)))
    send_bytes(p, bytes([0x1B, 0x64, n]))


def feed_dots(p, n: int) -> None:
    """Feed `n` dots (1/203 inch on typical 80mm units)."""
    n = max(0, min(255, int(n)))
    send_bytes(p, bytes([0x1B, 0x4A, n]))


# ---------- cut ----------

def cut(p, partial: bool = False) -> None:
    """Cut the paper. GS V m  (m=0 full cut, m=1 partial cut)."""
    send_bytes(p, bytes([0x1D, 0x56, 1 if partial else 0]))


def cut_after_feed(p, lines: int = 3, partial: bool = False) -> None:
    """Feed then cut — more forgiving on printers that need room."""
    feed_lines(p, lines)
    cut(p, partial)


# ---------- reset / init ----------

def reset(p) -> None:
    """ESC @ — initialize printer to defaults."""
    send_bytes(p, bytes([0x1B, 0x40]))


# ---------- density ----------

def set_density(p, level: int = 8) -> None:
    """Set print density. Command: GS ( K pL pH fn m — function 49 sets density.
    `level` is typically 0-15 (some models 0-8). Not all printers honor this."""
    level = max(0, min(15, int(level)))
    send_bytes(p, bytes([0x1D, 0x28, 0x4B, 0x02, 0x00, 0x31, level]))


# ---------- code page ----------

# A subset of common ESC/POS code pages (ESC t n)
CODE_PAGES: dict[int, str] = {
    0: "CP437 (US + standard Europe)",
    2: "CP850 (multilingual)",
    3: "CP860 (Portuguese)",
    4: "CP863 (Canadian French)",
    5: "CP865 (Nordic)",
    16: "WPC1252 (Windows Latin-1)",
    17: "CP866 (Cyrillic 2)",
    18: "CP852 (Latin 2)",
    19: "CP858 (euro)",
    20: "Thai 42",
    21: "Thai 11",
    22: "Thai 13",
}


def set_code_page(p, n: int) -> None:
    """ESC t n — switch character code table."""
    n = max(0, min(255, int(n)))
    send_bytes(p, bytes([0x1B, 0x74, n]))


# ---------- self-test ----------

def self_test(p) -> None:
    """Built-in printer self-test page.

    Not universally supported. Falls back to a printed demo if the command
    doesn't trigger anything on this model.
    """
    # Most common: GS ( A pL pH n m  -> print self-test pattern
    send_bytes(p, bytes([0x1D, 0x28, 0x41, 0x02, 0x00, 0x00, 0x02]))


# ---------- status ----------

STATUS_MODES = {
    1: "printer status",
    2: "offline cause",
    3: "error cause",
    4: "roll paper sensor",
}


def query_status(p, mode: int = 1, timeout_ms: int = 800) -> Optional[int]:
    """DLE EOT n — real-time status query. Returns the single status byte or
    None if the printer doesn't respond (or the backend can't read)."""
    mode = int(mode)
    if mode not in STATUS_MODES:
        raise ValueError(f"unknown status mode {mode}")
    send_bytes(p, bytes([0x10, 0x04, mode]))
    # Only USB/Serial backends can read back.
    try:
        data = p.device.read(p.in_ep, 64, timeout=timeout_ms)  # type: ignore[attr-defined]
    except Exception:
        return None
    if not data:
        return None
    return int(data[0])


def parse_status_byte(mode: int, b: int) -> dict:
    """Interpret a single status byte per ESC/POS conventions."""
    # Bit 4 is always set on status bytes; bits 0/1 always 0/1 respectively.
    flags: dict[str, bool] = {}
    if mode == 1:  # printer
        flags["drawer_open"] = bool(b & 0x04)      # low when open
        flags["online"] = not (b & 0x08)
        flags["cover_open"] = bool(b & 0x20)
        flags["feed_button_pressed"] = bool(b & 0x40)
    elif mode == 2:  # offline cause
        flags["cover_open"] = bool(b & 0x04)
        flags["paper_feed_button"] = bool(b & 0x08)
        flags["paper_ended"] = bool(b & 0x20)
        flags["error_occurred"] = bool(b & 0x40)
    elif mode == 3:  # error
        flags["cutter_error"] = bool(b & 0x08)
        flags["unrecoverable_error"] = bool(b & 0x20)
        flags["autorecoverable_error"] = bool(b & 0x40)
    elif mode == 4:  # paper sensor
        flags["near_end_paper_sensor"] = bool(b & 0x0C) == 0x0C
        flags["paper_end_sensor"] = bool(b & 0x60) == 0x60
    return flags


# ---------- raw console ----------

def parse_raw_input(text: str) -> bytes:
    """Parse user input into bytes.

    Accepts:
      - hex tokens separated by whitespace/commas:  "1b 40 48 69"
      - Python escape sequences: "\\x1b@Hello\\n"  (also \\e for ESC)
    Mixed lines are fine as long as each line picks a style.
    """
    text = text.strip()
    if not text:
        return b""

    # Python-escape path if we see backslash escapes.
    if re.search(r"\\[xenrt0\\]", text):
        normalized = text.replace("\\e", "\\x1b")
        try:
            return normalized.encode("utf-8").decode("unicode_escape").encode("latin-1")
        except Exception as e:
            raise ValueError(f"bad escape sequence: {e}")

    # Hex path.
    tokens = re.findall(r"[0-9a-fA-F]{2}", text)
    if not tokens:
        raise ValueError("enter hex bytes or escape sequences")
    return bytes(int(t, 16) for t in tokens)


def send_raw(p, text: str) -> int:
    """Send raw bytes parsed from `text`. Returns how many bytes were sent."""
    data = parse_raw_input(text)
    if not data:
        raise ValueError("nothing to send")
    send_bytes(p, data)
    return len(data)


# A tiny cheat sheet of common commands, exported for the UI.
CHEAT_SHEET = [
    ("ESC @", "1b 40", "initialize"),
    ("LF",    "0a",    "print + line feed"),
    ("FF",    "0c",    "form feed"),
    ("ESC E 1", "1b 45 01", "bold on"),
    ("ESC E 0", "1b 45 00", "bold off"),
    ("ESC ! n", "1b 21 30", "double width + height"),
    ("ESC a n", "1b 61 01", "align: 0 left, 1 center, 2 right"),
    ("ESC d n", "1b 64 03", "feed n lines"),
    ("ESC p 0 50 100", "1b 70 00 32 64", "drawer pin 2"),
    ("ESC B 3 5", "1b 42 03 05", "3 beeps, 500ms"),
    ("GS V 0",  "1d 56 00", "full cut"),
    ("GS V 1",  "1d 56 01", "partial cut"),
    ("GS B 1",  "1d 42 01", "inverse on"),
    ("ESC { 1", "1b 7b 01", "upside-down on"),
    ("ESC V 1", "1b 56 01", "rotate 90° clockwise"),
    ("DLE EOT 1", "10 04 01", "read printer status"),
]
