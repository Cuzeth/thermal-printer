"""Shared in-memory sliding-window rate limiting.

Used by both the friend login/register endpoints (auth/blueprint.py)
and the admin TOTP login (auth/admin.py). Buckets are plain dicts in
process memory — they reset on restart, which is a documented tradeoff
(single owner, small audience, single gunicorn worker). Don't add
persistence or Redis.
"""

from __future__ import annotations

import time

from flask import request


def client_ip() -> str:
    """Last hop in X-Forwarded-For (the one the trusted proxy appended),
    else remote_addr.

    The app only ever hears from cloudflared (bound to 127.0.0.1), and
    Cloudflare's edge appends the real client address as the *last* XFF
    entry. The first entry is client-controlled — proxies append, so a
    `curl -H "X-Forwarded-For: x"` would land first and let an attacker
    rotate fake addresses to bypass the per-IP buckets."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        last = xff.split(",")[-1].strip()
        if last:
            return last
    return request.remote_addr or "?"


def sliding_check(bucket: dict[str, list[float]], key: str,
                  limit: int, window: int) -> bool:
    """Return True if the key is already at/over the limit. Also prunes."""
    cutoff = time.time() - window
    arr = [t for t in bucket.get(key, []) if t > cutoff]
    if arr:
        bucket[key] = arr
    else:
        bucket.pop(key, None)
    return len(arr) >= limit


def sliding_record(bucket: dict[str, list[float]], key: str, window: int) -> None:
    cutoff = time.time() - window
    arr = [t for t in bucket.get(key, []) if t > cutoff]
    arr.append(time.time())
    bucket[key] = arr
