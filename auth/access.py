"""Cloudflare Access gating for the private console.

The console lives at console.cuzeth.com, a tunnel hostname cloudflared
only serves after validating a Cloudflare Access JWT — the ingress rule
in deploy/cloudflared-config.yml sets ``access.required`` plus the app's
audience tag, so unauthenticated requests die at the connector and never
reach Flask.  Access authenticates the visitor at the edge (the policy
says which emails may pass) and forwards the proven identity in
``Cf-Access-Authenticated-User-Email``.

Friend traffic at print.cuzeth.com never carries trustworthy Access
headers — no Access application covers that hostname, so a client could
send forged ones.  Three walls keep forgeries away from private routes:

1. the friend ingress path-allowlists only the friend surface, so
   private routes 404 at the tunnel (deploy/cloudflared-config.yml);
2. an edge Transform Rule strips ``Cf-Access-*`` headers from friend-host
   requests (dashboard-side, documented in DEPLOY.md);
3. this module pins the authenticated email to ``OWNER_EMAIL``, so even
   a widened Access policy or a fat-fingered allowlist admits nobody but
   the owner.

SECURITY: Flask must bind to 127.0.0.1 so that only cloudflared can
reach it.  If the app were reachable on 0.0.0.0, anyone on the LAN could
forge the Access headers with no edge in the way.
"""

from __future__ import annotations

from functools import wraps

from flask import abort, request

import config


def is_console_request() -> bool:
    """Return True if Cloudflare Access authenticated this request as the owner.

    In local dev, set ``DEV_BYPASS_ACCESS=true`` so ``python3 app.py``
    works without fake headers.
    """
    if config.DEV_BYPASS_ACCESS:
        return True
    if not config.OWNER_EMAIL:
        # Fail closed: without a pinned owner identity we can't tell the
        # owner from anyone else Access might have admitted. Set
        # OWNER_EMAIL in .env; until then every private route 403s.
        return False
    if not request.headers.get("Cf-Access-Jwt-Assertion"):
        return False
    email = request.headers.get("Cf-Access-Authenticated-User-Email", "")
    return email.strip().lower() == config.OWNER_EMAIL


def require_access(fn):
    """Decorator: reject requests that didn't come through Cloudflare
    Access as the owner, with 403.

    Apply this *outside* (above) any other auth decorator so public users
    are bounced immediately without leaking information about which routes
    exist or what further auth they require.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_console_request():
            abort(403)
        return fn(*args, **kwargs)
    return wrapper
