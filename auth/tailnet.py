"""Tailnet gating via Tailscale identity headers.

Tailscale's reverse proxy injects ``Tailscale-User-Login`` (and related
headers) for requests originating from the tailnet.  Public Funnel traffic
arrives without these headers.  We use their presence as a reliable signal
for "this request came from my tailnet."

SECURITY: Flask must bind to 127.0.0.1 so that only the local Tailscale
proxy can reach it.  If the app were reachable on 0.0.0.0, an attacker
could connect directly and forge the header.
"""

from __future__ import annotations

from functools import wraps

from flask import abort, request

import config


def is_tailnet_request() -> bool:
    """Return True if the current request originated from the tailnet.

    In local dev, set ``DEV_BYPASS_TAILNET=true`` so ``python3 app.py``
    works without fake headers.
    """
    if config.DEV_BYPASS_TAILNET:
        return True
    return bool(request.headers.get("Tailscale-User-Login"))


def require_tailnet(fn):
    """Decorator: reject non-tailnet requests with 403.

    Apply this *outside* (above) any other auth decorator so public users
    are bounced immediately without leaking information about which routes
    exist or what further auth they require.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_tailnet_request():
            abort(403)
        return fn(*args, **kwargs)
    return wrapper
