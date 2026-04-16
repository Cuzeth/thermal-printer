"""Shared pytest setup — force DRY_RUN and isolated DATA_DIR before any
project module imports. Individual tests never talk to USB."""

from __future__ import annotations

import os
import tempfile


_TMP = tempfile.mkdtemp(prefix="tp-pytest-")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("ADMIN_TOKEN", "pytest-token")
os.environ.setdefault("DEV_BYPASS_TAILNET", "true")
