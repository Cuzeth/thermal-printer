"""Printer configuration. Adjust these for your specific hardware.

All values can be overridden via environment variables — useful when running
in Docker on the NAS where the defaults below (loopback host, mac-only paths)
don't apply.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v is not None else default


def _env_hex(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None:
        return default
    return int(v, 0)


# ---------- printer hardware ----------

USB_VENDOR_ID = _env_hex("USB_VENDOR_ID", 0x0483)
USB_PRODUCT_ID = _env_hex("USB_PRODUCT_ID", 0x5720)
USB_OUT_EP = _env_hex("USB_OUT_EP", 0x03)
USB_IN_EP = _env_hex("USB_IN_EP", 0x81)

# Receipt width in characters at standard font. 32 for 58mm, 42 for 80mm.
RECEIPT_WIDTH = _env_int("RECEIPT_WIDTH", 42)

# Printer raster image width in pixels (typical 58mm = 384px, 80mm = 576px).
PRINTER_PIXEL_WIDTH = _env_int("PRINTER_PIXEL_WIDTH", 576)


# ---------- runtime ----------

# Bind 127.0.0.1 by default for local dev; container overrides to 0.0.0.0.
HOST = os.getenv("HOST", "127.0.0.1")
PORT = _env_int("PORT", 5005)

DRY_RUN = _env_bool("DRY_RUN", False)

DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).parent / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DRY_RUN_PATH = os.getenv("DRY_RUN_PATH", str(DATA_DIR / "last_print.bin"))


# ---------- auth (used in Phase 2+) ----------

# In dev we mint a fresh key each boot so sessions invalidate on restart.
# In prod (NAS), set SECRET_KEY in .env to keep sessions across restarts.
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)

# Long random string for admin endpoints (curl + Bearer header). Required
# in prod; in dev a fresh one is minted per boot and printed on startup.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN") or secrets.token_urlsafe(32)
_ADMIN_TOKEN_FROM_ENV = os.getenv("ADMIN_TOKEN") is not None

# WebAuthn relying-party identifiers. RP_ID must match the hostname the
# browser sees. In dev, "localhost" works. In prod set to the full
# Tailscale hostname, e.g. "printer-nas.tail-scales.ts.net".
RP_ID = os.getenv("RP_ID", "localhost")
RP_NAME = os.getenv("RP_NAME", "Thermal Printer")
ORIGIN = os.getenv("ORIGIN", f"http://localhost:{PORT}")

DB_PATH = DATA_DIR / "app.db"
