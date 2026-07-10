"""Tailnet gating via Tailscale identity headers.

Two local proxies talk to this app.  ``tailscale serve`` publishes it to
the tailnet only and injects ``Tailscale-User-Login`` (and related
headers) on every request.  ``cloudflared`` publishes the friends page to
the internet at print.cuzeth.com — and, unlike Tailscale's proxy, it
forwards whatever headers the client sent, so a public visitor could
forge the identity header.  Three walls stop that:

1. cloudflared's ingress allowlists only the friend paths, so private
   routes never reach Flask from the internet (deploy/cloudflared-config.yml);
2. a Cloudflare Transform Rule strips ``Tailscale-User-Login`` at the
   edge (dashboard-side, documented in DEPLOY.md);
3. this module refuses tailnet status to any request bearing ``CF-Ray``,
   the marker Cloudflare's edge stamps on every request it proxies.
   Clients can't remove it, so it's a reliable "did not come from
   tailscaled" signal even if walls 1 and 2 are misconfigured.

SECURITY: Flask must bind to 127.0.0.1 so that only the two local proxies
can reach it.  If the app were reachable on 0.0.0.0, an attacker could
connect directly and forge the header with no Cloudflare marker at all.
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
    # Anything that traversed Cloudflare is internet traffic by definition,
    # identity header or not — see the module docstring for why the header
    # alone can't be trusted on that path. A tailnet user *could* send a
    # fake CF-Ray and lock themselves out, but that fails closed and the
    # tailnet is trusted anyway.
    if request.headers.get("CF-Ray"):
        return False
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
