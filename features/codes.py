"""QR codes and 1D barcode support.

For previews, we render with `qrcode` + `python-barcode` into PIL images so
the receipt preview panel can show exactly what's about to print.

For actual printing, we use python-escpos's native barcode/QR commands so
the printer renders them at full resolution with its own ESC/POS engine —
much crisper than rasterizing ourselves.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

import qrcode
from qrcode.constants import (
    ERROR_CORRECT_H,
    ERROR_CORRECT_L,
    ERROR_CORRECT_M,
    ERROR_CORRECT_Q,
)
from PIL import Image

from barcode import get_barcode_class
from barcode.errors import BarcodeError
from barcode.writer import ImageWriter
from escpos.constants import (
    QR_ECLEVEL_H,
    QR_ECLEVEL_L,
    QR_ECLEVEL_M,
    QR_ECLEVEL_Q,
)


# ---------- QR ----------

_EC_MAP = {
    "L": ERROR_CORRECT_L,
    "M": ERROR_CORRECT_M,
    "Q": ERROR_CORRECT_Q,
    "H": ERROR_CORRECT_H,
}


@dataclass
class QROptions:
    data: str
    ec: str = "M"           # L / M / Q / H
    size: int = 8           # ESC/POS module size 1-16
    box_size: int = 10      # pixels per module in the PIL preview


def make_qr_image(opts: QROptions) -> Image.Image:
    if not opts.data:
        raise ValueError("QR payload is empty.")
    q = qrcode.QRCode(
        error_correction=_EC_MAP.get(opts.ec.upper(), ERROR_CORRECT_M),
        box_size=max(1, int(opts.box_size)),
        border=2,
    )
    q.add_data(opts.data)
    q.make(fit=True)
    img = q.make_image(fill_color="black", back_color="white")
    return img.convert("L")


# ---------- 1D Barcodes ----------

# Mapping UI label -> (python-barcode class name, ESC/POS name)
BARCODE_TYPES: dict[str, tuple[str, str]] = {
    "CODE128":  ("code128", "CODE128"),
    "CODE39":   ("code39",  "CODE39"),
    "EAN13":    ("ean13",   "EAN13"),
    "EAN8":     ("ean8",    "EAN8"),
    "UPC-A":    ("upca",    "UPC-A"),
    "ITF":      ("itf",     "ITF"),
    "CODABAR":  ("codabar", "CODABAR"),
}

HRI_POSITIONS = ["OFF", "ABOVE", "BELOW", "BOTH"]


@dataclass
class BarcodeOptions:
    kind: str       # key from BARCODE_TYPES
    data: str
    width: int = 3      # module width (1-6 in ESC/POS)
    height: int = 80    # module height in dots (1-255)
    hri: str = "BELOW"  # OFF / ABOVE / BELOW / BOTH
    font: str = "A"     # font for HRI text, A or B


def make_barcode_image(opts: BarcodeOptions) -> Image.Image:
    if opts.kind not in BARCODE_TYPES:
        raise ValueError(f"Unknown barcode type: {opts.kind}")
    if not opts.data:
        raise ValueError("Barcode payload is empty.")
    lib_name, _ = BARCODE_TYPES[opts.kind]
    try:
        cls = get_barcode_class(lib_name)
    except Exception as e:
        raise ValueError(f"barcode lib: {e}")
    try:
        # CODE39 is case-sensitive and refuses some chars — upper-case by default
        data = opts.data.upper() if opts.kind == "CODE39" else opts.data
        code = cls(data, writer=ImageWriter())
        buf = io.BytesIO()
        code.write(
            buf,
            options={
                "module_width": max(0.2, opts.width / 5.0),
                "module_height": max(4.0, opts.height / 5.0),
                "write_text": opts.hri in ("ABOVE", "BELOW", "BOTH"),
                "quiet_zone": 2.0,
                "font_size": 10,
                "text_distance": 2,
                "background": "white",
                "foreground": "black",
            },
        )
    except BarcodeError as e:
        raise ValueError(f"invalid data for {opts.kind}: {e}")
    buf.seek(0)
    return Image.open(buf).convert("L")


_ESCPOS_EC = {
    "L": QR_ECLEVEL_L,
    "M": QR_ECLEVEL_M,
    "Q": QR_ECLEVEL_Q,
    "H": QR_ECLEVEL_H,
}


def print_qr(p, opts: QROptions) -> None:
    size = max(1, min(16, int(opts.size)))
    ec = _ESCPOS_EC.get(opts.ec.upper(), QR_ECLEVEL_M)
    # python-escpos doesn't support `center=True` with `native=True`; emit the
    # alignment byte ourselves instead (ESC a 1 centers subsequent output).
    p.set(align="center")
    p.qr(opts.data, ec=ec, size=size, native=True)
    p.set(align="left")


def print_barcode(p, opts: BarcodeOptions) -> None:
    _, escpos_kind = BARCODE_TYPES[opts.kind]
    data = opts.data.upper() if opts.kind == "CODE39" else opts.data
    width = max(2, min(6, int(opts.width)))
    height = max(1, min(255, int(opts.height)))
    # python-escpos uses the new `barcode()` signature for CODE128 etc.
    p.set(align="center")
    p.barcode(
        data,
        escpos_kind,
        width=width,
        height=height,
        pos=opts.hri,
        font=opts.font,
        check=False,
    )
    p.set(align="left")
