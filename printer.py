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


@contextmanager
def open_printer() -> Iterator[object]:
    """Yield an ESC/POS printer instance, serialized across threads.

    In DRY_RUN mode, a Dummy printer collects bytes and writes them to disk
    instead of talking to USB. The resulting file can be `cat`-ed into the
    real printer later with `lp -o raw`.
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
        except Exception as e:
            raise PrinterError(
                f"Could not connect to printer ({config.USB_VENDOR_ID:#06x}:"
                f"{config.USB_PRODUCT_ID:#06x}): {e}"
            ) from e

        try:
            yield p
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
