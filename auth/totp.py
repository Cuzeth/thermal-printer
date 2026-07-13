"""Tiny RFC 6238 TOTP — the whole admin login scheme.

The owner's authenticator app and this module share one base32 secret
(TOTP_SECRET in .env). Logging into /admin means typing the current
6-digit code; there is no account, no password, no identity provider.
Stdlib only — SHA-1/30s/6-digit is deliberately the authenticator-app
default profile, so any app that can scan an otpauth:// QR works.

SHA-1 is fine here despite its reputation: TOTP uses HMAC-SHA1, whose
security rests on the secret key, not on collision resistance.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

STEP_SECONDS = 30
DIGITS = 6


def generate_secret() -> str:
    """A fresh 160-bit base32 secret (the size RFC 4226 recommends)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def _code_for_step(secret: str, step: int) -> str:
    # Authenticator apps are forgiving about padding and case; be the same.
    normalized = secret.strip().replace(" ", "").upper()
    key = base64.b32decode(normalized + "=" * (-len(normalized) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % 10 ** DIGITS).zfill(DIGITS)


def code_at(secret: str, at: float) -> str:
    """The code a correct authenticator shows at unix time `at`."""
    return _code_for_step(secret, int(at) // STEP_SECONDS)


def verify(secret: str, code: str, at: float | None = None,
           window: int = 1) -> int | None:
    """Return the matched timestep if `code` is valid, else None.

    `window=1` accepts the previous and next step too (±30s) so a code
    typed near a boundary, or a slightly-skewed phone clock, still works.
    Returning the step (not just True) lets the caller refuse to accept
    the same step twice — TOTP's replay guard.
    """
    if not secret or not code:
        return None
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    now_step = int(at if at is not None else time.time()) // STEP_SECONDS
    for step in range(now_step - window, now_step + window + 1):
        if hmac.compare_digest(_code_for_step(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, label: str = "thermal-printer") -> str:
    """The URI authenticator apps enroll from (usually via QR)."""
    return (f"otpauth://totp/{label}?secret={secret}"
            f"&issuer={label}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")
