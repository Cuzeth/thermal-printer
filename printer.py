"""Thermal printer abstraction.

Wraps python-escpos with a context manager so every request acquires a fresh
USB handle and releases it cleanly. Supports a DRY_RUN mode that writes raw
ESC/POS bytes to a file instead of the printer — great for iterating on
layouts without wasting a roll of paper.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from escpos.printer import Dummy, Usb

import config


_lock = threading.Lock()


class PrinterError(RuntimeError):
    pass


# Exception classes we treat as "printer offline" — map to PrinterError so
# routes return a clean 503 instead of leaking escpos/pyusb internals.
# Using lazy name-matching (rather than `except EscposException`) because
# escpos.exceptions has shuffled class names across minor versions.
_OFFLINE_NAMES = {
    "DeviceNotFoundError",   # escpos 3.x
    "USBNotFoundError",      # escpos 3.x
    "USBError",              # pyusb
    "NoBackendError",        # pyusb — libusb missing
}


def _looks_offline(e: BaseException) -> bool:
    return type(e).__name__ in _OFFLINE_NAMES or isinstance(e, AssertionError)


def _offline_msg(e: BaseException) -> str:
    return (
        f"Printer not responding ({config.USB_VENDOR_ID:#06x}:"
        f"{config.USB_PRODUCT_ID:#06x}). Check the USB cable + power. "
        f"[{type(e).__name__}]"
    )


@contextmanager
def open_printer() -> Iterator[object]:
    """Yield an ESC/POS printer instance, serialized across threads.

    In DRY_RUN mode, a Dummy printer collects bytes and writes them to disk
    instead of talking to USB. The resulting file can be `cat`-ed into the
    real printer later with `lp -o raw`.

    Errors (missing device, cable unplugged, libusb missing) are mapped to
    PrinterError so callers don't have to know the escpos/pyusb exception
    tree. The mapping runs both at open time (eager) and around the yield
    (late I/O), because escpos 3.x defers actual USB enumeration until the
    first write.
    """
    with _lock:
        if config.DRY_RUN:
            p = Dummy()
            try:
                yield p
            finally:
                with open(config.DRY_RUN_PATH, "wb") as f:
                    f.write(p.output)
            return

        try:
            p = Usb(
                config.USB_VENDOR_ID,
                config.USB_PRODUCT_ID,
                out_ep=config.USB_OUT_EP,
                in_ep=config.USB_IN_EP,
            )
            # Force eager device lookup. Without this, "cable unplugged"
            # surfaces halfway through a print as a bare AssertionError.
            if hasattr(p, "open"):
                p.open()
        except PrinterError:
            raise
        except Exception as e:
            if _looks_offline(e):
                raise PrinterError(_offline_msg(e)) from e
            raise PrinterError(
                f"Could not connect to printer ({config.USB_VENDOR_ID:#06x}:"
                f"{config.USB_PRODUCT_ID:#06x}): {e}"
            ) from e

        try:
            yield p
        except Exception as e:
            if _looks_offline(e):
                raise PrinterError(_offline_msg(e)) from e
            raise
        finally:
            try:
                p.close()
            except Exception:
                pass


def hr(p, char: str = "-") -> None:
    """Print a horizontal rule the full width of the receipt."""
    p.text(char * config.RECEIPT_WIDTH + "\n")


def center(p, text: str, bold: bool = False) -> None:
    p.set(align="center", bold=bold)
    p.text(text + "\n")
    p.set(align="left", bold=False)


def heading(p, text: str) -> None:
    """A nice centered bold double-height heading."""
    p.set(align="center", bold=True, double_height=True, double_width=True)
    p.text(text + "\n")
    p.set(align="left", bold=False, double_height=False, double_width=False)


def footer(p) -> None:
    p.text("\n")
    p.cut()
