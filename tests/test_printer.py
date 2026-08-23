"""Printer context manager — verify that USB errors get mapped to
PrinterError with a friendly message, regardless of whether the underlying
library raises at construction time or during first I/O."""

from __future__ import annotations

import pytest

import config
import printer


class _FakeEagerFail:
    """Mimics escpos >=3: constructor succeeds, .open() raises."""
    def __init__(self, *args, **kwargs):
        pass
    def open(self):
        raise type("DeviceNotFoundError", (Exception,), {})("cable unplugged")
    def close(self):
        pass


class _FakeLateFail:
    """Constructor + open succeed; the yielded object blows up on I/O."""
    def __init__(self, *args, **kwargs):
        pass
    def open(self):
        pass
    def image(self, *_a, **_kw):
        raise type("USBError", (Exception,), {})("pipe error")
    def close(self):
        pass


@pytest.fixture
def live_mode(monkeypatch):
    """Temporarily flip out of DRY_RUN so we exercise the USB branch."""
    monkeypatch.setattr(config, "DRY_RUN", False)


def test_eager_open_failure_raises_printer_error(live_mode, monkeypatch):
    monkeypatch.setattr(printer, "Usb", _FakeEagerFail)
    with pytest.raises(printer.PrinterError) as ei:
        with printer.open_printer():
            pass
    msg = str(ei.value)
    assert "printer offline" in msg
    assert "DeviceNotFoundError" in msg


def test_late_io_failure_raises_printer_error(live_mode, monkeypatch):
    monkeypatch.setattr(printer, "Usb", _FakeLateFail)
    with pytest.raises(printer.PrinterError) as ei:
        with printer.open_printer() as p:
            p.image("fake")
    assert "printer offline" in str(ei.value)


def test_non_device_exception_is_not_masked(live_mode, monkeypatch):
    class _RandomFail:
        def __init__(self, *a, **k): pass
        def open(self): pass
        def close(self): pass

    monkeypatch.setattr(printer, "Usb", _RandomFail)
    with pytest.raises(ValueError, match="unrelated"):
        with printer.open_printer() as p:
            raise ValueError("unrelated")


def test_dry_run_still_yields_dummy():
    # Belt-and-suspenders: the normal DRY_RUN path shouldn't regress.
    with printer.open_printer() as p:
        assert hasattr(p, "_raw") or hasattr(p, "output")


def test_status_flips_on_offline_open_and_recovers(live_mode, monkeypatch):
    # _FakeEagerFail triggers the recovery path, which calls the real
    # usb.core.find (no device on this dev machine -> returns None ->
    # clean PrinterError). The other tests in this file already rely on
    # this same fallthrough.
    monkeypatch.setattr(printer, "Usb", _FakeEagerFail)
    with pytest.raises(printer.PrinterError):
        with printer.open_printer():
            pass
    assert printer.status()["ok"] is False

    class _WorksFine:
        def __init__(self, *a, **k): pass
        def open(self): pass
        def close(self): pass

    monkeypatch.setattr(printer, "Usb", _WorksFine)
    with printer.open_printer():
        pass
    assert printer.status()["ok"] is True


def test_dead_handle_after_open_triggers_reset_and_reopen(live_mode, monkeypatch):
    """escpos' open() can return without raising while holding a handle
    the kernel already dropped (it swallows the set_configuration error).
    The post-open probe must surface that as an open-time failure so the
    USB reset + reopen path runs, instead of the first write dying."""
    opens = []

    class _DeadDevice:
        def get_active_configuration(self):
            raise type("USBError", (Exception,), {})("[Errno 19] No such device")

    class _LiveDevice:
        def get_active_configuration(self):
            return object()

    class _FlakyUsb:
        def __init__(self, *a, **k):
            opens.append(self)
            self.device = _DeadDevice() if len(opens) == 1 else _LiveDevice()
        def open(self): pass
        def close(self): pass

    monkeypatch.setattr(printer, "Usb", _FlakyUsb)
    monkeypatch.setattr(printer, "reset_device", lambda: True)
    with printer.open_printer() as p:
        assert isinstance(p.device, _LiveDevice)
    assert len(opens) == 2
    assert printer.status()["ok"] is True


def test_dead_handle_with_device_gone_is_a_clean_offline_error(live_mode, monkeypatch):
    """Same dead handle, but the reset finds nothing on the bus: the
    friend-facing 'printer offline' error, not a pyusb traceback."""
    class _DeadDevice:
        def get_active_configuration(self):
            raise type("USBError", (Exception,), {})("[Errno 19] No such device")

    class _DeadUsb:
        def __init__(self, *a, **k):
            self.device = _DeadDevice()
        def open(self): pass
        def close(self): pass

    monkeypatch.setattr(printer, "Usb", _DeadUsb)
    monkeypatch.setattr(printer, "reset_device", lambda: False)
    with pytest.raises(printer.PrinterError) as ei:
        with printer.open_printer():
            pass
    assert "printer offline" in str(ei.value)
    assert printer.status()["ok"] is False
