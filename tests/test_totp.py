"""auth/totp.py against the RFC 6238 Appendix B test vectors.

The RFC publishes 8-digit SHA-1 codes for the ASCII secret
"12345678901234567890"; our 6-digit codes are the same truncated value
mod 10^6, i.e. the vector's last six digits. If these pass, the module
agrees with every authenticator app on the planet.
"""

from __future__ import annotations

from auth import totp

# base32("12345678901234567890") — same secret conftest wires into config.
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

RFC_VECTORS = [
    (59, "287082"),           # RFC: 94287082
    (1111111109, "081804"),   # RFC: 07081804
    (1111111111, "050471"),   # RFC: 14050471
    (1234567890, "005924"),   # RFC: 89005924
    (2000000000, "279037"),   # RFC: 69279037
]


def test_rfc6238_vectors():
    for at, expected in RFC_VECTORS:
        assert totp.code_at(SECRET, at) == expected


def test_verify_accepts_current_code_and_returns_step():
    at = 1111111111
    step = totp.verify(SECRET, "050471", at=at)
    assert step == at // totp.STEP_SECONDS


def test_verify_accepts_adjacent_steps_only():
    """window=1 tolerates a slightly-skewed phone clock (±30s) but no
    more — an hour-old code must be dead."""
    at = 1111111111
    prev_code = totp.code_at(SECRET, at - 30)
    next_code = totp.code_at(SECRET, at + 30)
    stale_code = totp.code_at(SECRET, at - 3600)
    assert totp.verify(SECRET, prev_code, at=at) is not None
    assert totp.verify(SECRET, next_code, at=at) is not None
    assert totp.verify(SECRET, stale_code, at=at) is None


def test_verify_rejects_garbage_input():
    at = 1111111111
    for bad in ("", "abc123", "05047", "0504711", "05 04 7"):
        assert totp.verify(SECRET, bad, at=at) is None
    # Spaces inside an otherwise-correct code are forgiven (phones love
    # to render codes as "050 471").
    assert totp.verify(SECRET, "050 471", at=at) is not None
    assert totp.verify(SECRET, "  050471  ", at=at) is not None


def test_verify_without_secret_is_none():
    assert totp.verify("", "123456", at=59) is None


def test_generate_secret_roundtrips():
    """A freshly minted secret is valid base32 that code_at can consume,
    and two mints never collide."""
    s1, s2 = totp.generate_secret(), totp.generate_secret()
    assert s1 != s2
    assert totp.code_at(s1, 59).isdigit() and len(totp.code_at(s1, 59)) == 6


def test_otpauth_uri_carries_the_secret():
    uri = totp.otpauth_uri("ABC234")
    assert uri.startswith("otpauth://totp/")
    assert "secret=ABC234" in uri and "digits=6" in uri and "period=30" in uri
