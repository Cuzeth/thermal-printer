"""Experimental RGB LED control for printers with status lights.

The Milestone P80E advertises "Audio-Visual Alerts with custom RGB lighting"
but the exact command sequence is vendor-specific and not published in any
documentation I could find. Rather than guess one and bake it in, this
module exposes several candidate protocols that are plausible across
Chinese 80mm thermal printers so the user can try them all and see which
the unit honors.

If none of these work, the fix is to download Milestone's SDK and read the
byte sequence from a packet capture or their sample code, then paste the
template into the raw Console tab.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from features.hardware import send_bytes


@dataclass
class Protocol:
    key: str
    name: str
    note: str
    build: Callable[[int, int, int], bytes]


def _esc_c_rgb(r: int, g: int, b: int) -> bytes:
    """ESC 'C' r g b — 5 bytes. Used by a handful of vendors as a compact
    color-set command."""
    return bytes([0x1B, 0x43, r, g, b])


def _gs_n_rgb(r: int, g: int, b: int) -> bytes:
    """GS 'N' 01 r g b — 6 bytes. A common "notification LED" extension."""
    return bytes([0x1D, 0x4E, 0x01, r, g, b])


def _gs_paren_c(r: int, g: int, b: int) -> bytes:
    """GS ( C pL pH fn r g b — the ESC/POS-style extension-table format."""
    return bytes([0x1D, 0x28, 0x43, 0x04, 0x00, 0x01, r, g, b])


def _mht_custom(r: int, g: int, b: int) -> bytes:
    """GS 0x8C r g b — a plausible Milestone/MHT custom sequence seen in
    some Chinese vendor SDKs. Worth trying for the P80E specifically."""
    return bytes([0x1D, 0x8C, r, g, b])


def _three_bit(r: int, g: int, b: int) -> bytes:
    """ESC 'L' n — 3-bit RGB mapped into a single color index (0-7).
    Many older units only support fixed color slots."""
    idx = 0
    if r > 96:
        idx |= 1
    if g > 96:
        idx |= 2
    if b > 96:
        idx |= 4
    return bytes([0x1B, 0x4C, idx])


PROTOCOLS: dict[str, Protocol] = {
    p.key: p
    for p in [
        Protocol(
            key="esc_c",
            name="ESC C r g b",
            note="5-byte direct RGB vendor command",
            build=_esc_c_rgb,
        ),
        Protocol(
            key="gs_n",
            name="GS N 01 r g b",
            note="notification LED extension",
            build=_gs_n_rgb,
        ),
        Protocol(
            key="gs_paren_c",
            name="GS ( C ...",
            note="ESC/POS extension-table format",
            build=_gs_paren_c,
        ),
        Protocol(
            key="mht_custom",
            name="GS 0x8C r g b",
            note="possible Milestone/MHT sequence",
            build=_mht_custom,
        ),
        Protocol(
            key="three_bit",
            name="ESC L n (3-bit)",
            note="fixed 8-color slots",
            build=_three_bit,
        ),
    ]
}


def build_bytes(protocol: str, r: int, g: int, b: int) -> bytes:
    if protocol not in PROTOCOLS:
        raise ValueError(f"unknown protocol: {protocol}")
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    return PROTOCOLS[protocol].build(r, g, b)


def send_color(p, protocol: str, r: int, g: int, b: int) -> bytes:
    data = build_bytes(protocol, r, g, b)
    send_bytes(p, data)
    return data


def hex_preview(data: bytes) -> str:
    return " ".join(f"{byte:02x}" for byte in data)
