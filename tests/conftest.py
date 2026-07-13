"""Shared pytest setup — force DRY_RUN and isolated DATA_DIR before any
project module imports. Individual tests never talk to USB."""

from __future__ import annotations

import os
import tempfile


_TMP = tempfile.mkdtemp(prefix="tp-pytest-")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("ADMIN_TOKEN", "pytest-token")
# The RFC 6238 test-vector secret (base32 of ASCII "12345678901234567890").
# Tests mint valid codes from it with auth.totp.code_at — no bypass flag,
# so the admin gate runs for real in every test.
os.environ.setdefault("TOTP_SECRET", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
