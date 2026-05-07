"""Thermal printer abstraction.

Wraps python-escpos with a context manager so every request acquires a fresh
USB handle and releases it cleanly. Supports a DRY_RUN mode that writes raw
ESC/POS bytes to a file instead of the printer — great for iterating on
layouts without wasting a roll of paper.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator

import usb.core
import usb.util
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


def reset_device() -> bool:
    """Issue a USB port reset to the printer — software unplug-replug.

    Long-running prints occasionally leave the bulk OUT endpoint halted or
    libusb's view of the device wedged; the symptom is `DeviceNotFoundError`
    on every subsequent open until the cable is physically reseated.
    `dev.reset()` causes the kernel to drop and re-enumerate the device,
    which clears both states without anyone touching the cable.

    Returns True if a device was found and reset, False if no matching
    device is present (i.e. it really is unplugged).
    """
    dev = usb.core.find(
        idVendor=config.USB_VENDOR_ID,
        idProduct=config.USB_PRODUCT_ID,
    )
    if dev is None:
        return False
    try:
        usb.util.dispose_resources(dev)
    except Exception:
        pass
    try:
        dev.reset()
    except Exception:
        # Reset itself can raise USBError as the device drops off the bus
        # mid-call. That's fine — re-enumeration still happens.
        pass
    # Give the kernel time to re-enumerate before the next open() races it.
    time.sleep(0.6)
    return True


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

        def _try_open():
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
            return p

        try:
            p = _try_open()
        except PrinterError:
            raise
        except Exception as e:
            # Recover from a wedged endpoint or stale handle — issuing a
            # port reset is the software equivalent of unplug-replug.
            if _looks_offline(e) and reset_device():
                try:
                    p = _try_open()
                except Exception as e2:
                    raise PrinterError(_offline_msg(e2)) from e2
            elif _looks_offline(e):
                raise PrinterError(_offline_msg(e)) from e
            else:
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


def print_image(p, img) -> None:
    """Send a PIL image to the printer with a buffer-safe fragment height.

    python-escpos' default fragment_height=960 produces single GS v 0
    transfers large enough to overrun the raster buffer on cheap printers,
    which then dumps the remaining bitmap bytes as text — visible as
    pages of garbled symbols. Routing every print through this helper
    keeps each fragment under the buffer limit.
    """
    p.image(img, fragment_height=config.IMAGE_FRAGMENT_HEIGHT)


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
