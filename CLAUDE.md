# CLAUDE.md

Hobby project: a web GUI for an 80mm USB thermal receipt printer on a
Raspberry Pi, plus a public "send me a receipt" page for friends
(`/m/`, public at print.cuzeth.com via Cloudflare Tunnel; the console
stays tailnet-only via `tailscale serve`). Tone of the codebase: lean
and playful, not enterprise. Prefer the smallest change that keeps the
comments honest.

## Run / test

```sh
source .venv/bin/activate
DEV_BYPASS_TAILNET=true COOKIE_SECURE=false python3 app.py   # dev server :5005
python -m pytest -q                                          # full suite, ~2s
DRY_RUN=true ADMIN_TOKEN=t DATA_DIR=/tmp/tp-smoke python scripts/test_auth_flow.py
```

- `DRY_RUN=true` writes ESC/POS bytes to `data/last_print.bin` instead of
  USB — use it for anything print-shaped; tests force it in
  `tests/conftest.py`.
- CI (`.github/workflows/ci.yml`) runs pytest + the auth smoke on Python
  3.12. There is no linter or typechecker; don't add one as a side effect.

## Load-bearing constraints — do not "fix" these

- **Single gunicorn worker.** The systemd unit runs `--workers 1
  --threads 4`. The USB lock (`printer.py:_lock`), the friend print queue
  (`app.py:_PRINT_QUEUE`), the in-flight caps, and the login rate-limit
  buckets all live in process memory. Adding workers silently breaks all
  of them.
- **Bind 127.0.0.1 only.** Private routes trust the
  `Tailscale-User-Login` header (`auth/tailnet.py`); that is only safe
  because nothing but the two local proxies (tailscaled serve +
  cloudflared) can reach the port. cloudflared forwards client headers
  verbatim, so three walls guard against forged identity headers: the
  tunnel's path allowlist (`deploy/cloudflared-config.yml`), an edge
  Transform Rule stripping the header, and the CF-Ray check in
  `auth/tailnet.py` — don't weaken any of them. Never bind 0.0.0.0,
  never set `DEV_BYPASS_TAILNET` or `FLASK_DEBUG` in anything
  deploy-shaped (the Werkzeug debugger on an internet-exposed app is RCE).
- **Render constants are hardware-validated.** Font sizes and spacing in
  `features/render.py` look wrong in PIL previews but print correctly on
  the real 80mm printer. Do not retune them from previews.
- **In-memory rate limits reset on restart — by design.** Documented
  tradeoff (`auth/blueprint.py:29`); don't add persistence or Redis.
- **`IMAGE_FRAGMENT_HEIGHT` (config.py) is a buffer-overrun workaround**
  for cheap printers — see the comment there before touching image
  chunking.

## Conventions

- All JSON responses: `{"ok": bool, ...}`; errors add `"error": <msg>` and
  `"kind": <"input"|"printer"|"server"|...>`. Owner POST routes wrap
  handlers in `_safe()` (`app.py`); friend routes return the same shape
  inline.
- Route gating: private console = `@require_tailnet` + `@require_owner`;
  admin = `@require_tailnet` + `@require_admin`; friend routes =
  `@require_allowed` (session). Every new route must pick one deliberately.
- Comments explain *why*, in full sentences; load-bearing decisions get a
  paragraph. Match that voice. Tests carry contract-stating docstrings and
  unique per-test usernames (`hist_alice`, `q_flood`, ...).
- Frontend is vanilla JS, no build step, DOM built via
  `createElement`/`textContent` — never `innerHTML`.
- Markup grammar lives in `features/markup.py` (shared by composer,
  friends page, and renderer). Extend it there, not with ad-hoc regexes.
- No emoji anywhere: not in UI copy, print templates, or widget output
  (owner decision, 2026-07-06 — recorded in plans/README.md; the repo's
  only emoji *rendering* is the per-character fallback route in
  features/render.py, which is what lets manually typed emoji in friend
  messages print however the fonts render them — keep that, add nothing).

## Verification etiquette

- After changes, run the full suite + auth smoke (commands above).
- Anything visual/print-shaped: use `DRY_RUN`, then hand the owner a short
  "to test:" checklist for the real printer. Don't set up browser
  automation.
