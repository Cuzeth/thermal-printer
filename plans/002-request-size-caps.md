# Plan 002: Cap request-body sizes and reject bad uploads with 400s, not 500s

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 2b723bc..HEAD -- app.py features/image.py tests/test_image.py tests/test_routes.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `2b723bc`, 2026-07-06

## Why this matters

The app runs on a Raspberry Pi and part of it (`/m/*`) is on the public
internet via Tailscale Funnel. Flask has **no `MAX_CONTENT_LENGTH`
configured anywhere** (`rg -n "MAX_CONTENT_LENGTH" .` → no matches), so
any client — including an unauthenticated visitor hitting a public route
like `/api/m/auth/login` — can POST an arbitrarily large body and the
server will buffer it into RAM before route code ever runs. The message
length check (`_MAX_MSG_LEN = 800`) happens *after* the whole JSON body is
parsed. On a Pi with limited memory that is a cheap way to knock the app
over by accident or on purpose.

Secondarily, the image pipeline decodes uploads before any dimension
check, and non-image garbage produces a 500 (`kind: "server"`) rather than
the 400 (`kind: "input"`) every other bad input gets.

None of this is currently exploited or broken in normal use — this is
defensive hardening of the public surface, sized to a hobby project.

## Current state

Relevant files:

- `app.py` — `app.config.update(...)` block at lines 35–45 (where the cap
  goes); `_safe()` error wrapper at lines 136–149; image endpoints
  `image_preview` (lines 209–221) and `print_image` (lines 223–243).
- `features/image.py` — `process()` decodes uploads (lines 37–61); the
  existing output-height guard is the convention to imitate.
- `tests/test_image.py` — image pipeline tests; `_png_bytes()` helper at
  lines 14–18.
- `tests/test_routes.py` — HTTP-surface tests; `client`/`auth` fixtures at
  lines 12–19.

`app.py:35–45` as of `2b723bc`:

```python
app.config.update(
    SECRET_KEY=config.SECRET_KEY,
    # Secure requires HTTPS — true on the Pi (Funnel), false in local dev.
    # Gated by an explicit env var rather than FLASK_DEBUG because debug is
    # usually off in dev too, which would silently kill the session cookie.
    SESSION_COOKIE_SECURE=config.COOKIE_SECURE,
    SESSION_COOKIE_HTTPONLY=True,
    # Lax + POST-only state changes = no CSRF surface worth protecting.
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,  # 30 days
)
```

`features/image.py:37–47` as of `2b723bc` (start of `process()`):

```python
def process(image_bytes: bytes, opts: ProcessOptions) -> Image.Image:
    img = Image.open(io.BytesIO(image_bytes))

    # Handle transparency -> white background
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        ...
```

The convention to imitate — the existing guard in the same file
(`features/image.py:20–24`), a module constant with a why-comment:

```python
# Hard ceiling on the processed image height. Width is clamped to the
# printer, but height scales proportionally — a 100×20,000 upload would
# become 576×115,200 after resize: an OOM candidate on a Pi Zero's 512MB
# and ~14 meters of paper. 4096px ≈ half a meter of receipt.
MAX_OUTPUT_HEIGHT = 4096
```

Error-shape convention: `{"ok": False, "error": <msg>, "kind": <tag>}`,
where `ValueError` → 400 `"input"`, unexpected → 500 `"server"` (see
`_safe()` at `app.py:136–149`).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Activate venv | `source .venv/bin/activate` | prompt shows `(.venv)` |
| Full test suite | `python -m pytest -q` | all pass (82 before, 86 after) |
| Image tests | `python -m pytest tests/test_image.py tests/test_routes.py -q` | all pass |

## Scope

**In scope** (the only files you should modify):
- `app.py` — the `app.config.update` block + one new 413 error handler
- `features/image.py` — `process()` and one new module constant
- `tests/test_image.py`, `tests/test_routes.py` — add tests

**Out of scope** (do NOT touch, even though they look related):
- `_MAX_MSG_LEN` and the friend-message length checks — already correct.
- `features/hardware.py` raw-bytes cap (4096 bytes) — already enforced.
- `MAX_OUTPUT_HEIGHT` and the resize logic in `features/image.py` — works,
  tested, and print-validated; don't retune it.
- gunicorn/systemd config in `deploy/` — the Flask-level cap is the fix;
  don't add proxy-level limits.

## Git workflow

- Branch: `advisor/002-request-size-caps`
- Single commit, repo style, e.g. `Harden uploads: global body cap, 413 as
  JSON, bad images as 400s`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Global request-body cap + JSON 413

In `app.py`, add to the `app.config.update(...)` block:

```python
    # Backstop for the public routes: nobody legitimately sends more than
    # an image upload here, and the Pi has to buffer whatever arrives.
    # Werkzeug answers oversized bodies with 413 before route code runs.
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # 16MB
```

Then add a 413 handler near `_security_headers` (after line ~59) so the
JSON-speaking frontends don't get an HTML error page:

```python
@app.errorhandler(413)
def _too_large(e):
    return jsonify({"ok": False, "error": "request too large (max 16MB)",
                    "kind": "input"}), 413
```

**Scope amendment (added in review, 2026-07-06):** werkzeug raises
`RequestEntityTooLarge` lazily, when the route body first reads the
request stream — for `_safe()`-wrapped routes that happens *inside* the
handler, where the bare `except Exception` converts it to a 500 before
Flask's 413 handler can run. Fix in `_safe()`: add a re-raise arm above
the catch-all, with `from werkzeug.exceptions import HTTPException` at
the top of `app.py`:

```python
    except HTTPException:
        # Let Flask's own error handlers answer (e.g. the JSON 413 above).
        # Without this, the catch-all turns a body-too-large abort into a
        # misleading 500.
        raise
```

placed after the `except ValueError` arm and before `except Exception`.

**Verify**: `python -m pytest -q` → all 82 existing tests still pass.

### Step 2: Reject non-images and absurd dimensions before decode work

In `features/image.py`:

a. Add a module constant next to `MAX_OUTPUT_HEIGHT`, same comment style:

```python
# Ceiling on *input* pixels, checked before any decode/convert work.
# Image.open() only reads the header, so width/height are known cheaply;
# .convert("RGB") is what actually materializes the bitmap (3 bytes per
# pixel — 30M px ≈ 90MB, about the most a small Pi should be asked to
# hold for a hobby print). Any phone photo fits comfortably.
MAX_INPUT_PIXELS = 30_000_000
```

b. At the top of `process()`, wrap the open and add the guards:

```python
def process(image_bytes: bytes, opts: ProcessOptions) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        # PIL raises UnidentifiedImageError (an OSError) on non-image
        # bytes. Surface it as input error (400), not a server 500.
        raise ValueError("that file doesn't look like an image") from e
    if img.width < 1 or img.height < 1:
        raise ValueError("image has no pixels")
    if img.width * img.height > MAX_INPUT_PIXELS:
        raise ValueError(
            f"image is {img.width}×{img.height} "
            f"(max {MAX_INPUT_PIXELS:,} pixels) — resize it first"
        )
```

The rest of `process()` is unchanged.

**Verify**: `python -m pytest tests/test_image.py -q` → all pass.

### Step 3: Tests

In `tests/test_image.py` (model on the existing `test_rejects_output_taller_than_cap`,
lines 69–75; reuse `_png_bytes`):

1. `test_rejects_non_image_bytes` — `image_feat.process(b"definitely not a png",
   image_feat.ProcessOptions())` inside `pytest.raises(ValueError,
   match="look like an image")`.
2. `test_rejects_too_many_input_pixels` — build a PNG over the cap without
   allocating 90MB: `Image.new("1", (8000, 4000))` is a 1-bit canvas
   (~4MB) but 32M pixels > `MAX_INPUT_PIXELS`. Save to PNG bytes like
   `_png_bytes` does (mode "1" instead of "RGB"), then
   `pytest.raises(ValueError, match="pixels")`.

In `tests/test_routes.py` (model on `test_hw_raw_rejects_oversized_payload`,
lines 60–65; use the `client` and `auth` fixtures):

3. `test_oversized_body_returns_json_413(client, auth)` — POST to
   `/api/image/preview` with `data={"file": (io.BytesIO(b"\0" * (17 * 1024 * 1024)), "big.png")}`
   and `headers=auth` → assert `r.status_code == 413`, `r.get_json()["ok"] is False`,
   `r.get_json()["kind"] == "input"`.
4. `test_image_preview_rejects_garbage_upload(client, auth)` — POST to
   `/api/image/preview` with `data={"file": (io.BytesIO(b"not an image"), "x.png")}`
   and `headers=auth` → assert 400 and `kind == "input"`.

**Verify**: `python -m pytest -q` → 86 passed.

## Test plan

Covered by Step 3: happy paths already exist in `tests/test_image.py`;
the four new tests pin the 413 shape, the non-image 400, and both new
guards. Full-suite + smoke:

- `python -m pytest -q` → 86 passed
- `DRY_RUN=true ADMIN_TOKEN=t DATA_DIR=/tmp/tp-smoke python scripts/test_auth_flow.py` → exits 0

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `python -m pytest -q` exits 0 with 86 passed
- [ ] `rg -n "MAX_CONTENT_LENGTH" app.py` returns one match
- [ ] `rg -n "MAX_INPUT_PIXELS" features/image.py` returns ≥2 matches
      (constant + guard)
- [ ] `git status --porcelain` shows changes only to `app.py`,
      `features/image.py`, `tests/test_image.py`, `tests/test_routes.py`
      (and `plans/README.md`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The `app.config.update` block or `process()` doesn't match the "Current
  state" excerpts (drift since `2b723bc`).
- The 413 test fails because the test client doesn't enforce
  `MAX_CONTENT_LENGTH` — investigate briefly (werkzeug does enforce it for
  file uploads); if it genuinely doesn't fire, report rather than raising
  the cap or faking the test.
- Any existing test in `tests/test_image.py` starts failing — the guards
  must not reject anything the current suite accepts (largest legal
  fixture is 576×4096).
- You are tempted to change `MAX_OUTPUT_HEIGHT`, resize behavior, or
  dithering — that's out of scope and print-validated on real hardware.

## Maintenance notes

- If a friend-facing image/doodle feature is ever added (a direction idea
  in `plans/README.md`), these same guards protect it — but the 16MB
  global cap and 30M-pixel input cap should then be revisited downward for
  the public route specifically.
- Reviewer should scrutinize: the `ValueError` wrap in Step 2 must not
  swallow non-PIL bugs — it only wraps `Image.open`, not the whole
  pipeline.
