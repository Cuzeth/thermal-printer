# Plan 004: Write a CLAUDE.md so agents inherit the load-bearing constraints

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 2b723bc..HEAD -- README.md DEPLOY.md app.py config.py`
> This plan only *creates* `CLAUDE.md`; if the files above changed since
> `2b723bc`, re-verify the facts in the draft below against them before
> writing (commands, ports, defaults).

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: dx
- **Planned at**: commit `2b723bc`, 2026-07-06

## Why this matters

Nearly every commit in this repo is agent-co-authored, and the repo has no
`CLAUDE.md` — so every session rediscovers the same non-obvious,
load-bearing constraints from scratch (or worse, violates one: an agent
that "helpfully" bumps gunicorn to `--workers 4` breaks the in-memory
print queue; one that "fixes" the render font sizes based on PIL previews
regresses real prints that were already validated on hardware). A short
CLAUDE.md is the cheapest way to make every future agent session start
competent. It also serves as the conventions reference the other plans in
this directory point at.

## Current state

- No `CLAUDE.md` or `AGENTS.md` exists (`ls CLAUDE.md AGENTS.md` → no such
  file).
- The facts the draft below encodes were verified at `2b723bc` against:
  `README.md` (quick start, security model, config table), `DEPLOY.md`
  (runbook), `config.py` (defaults), `app.py` (queue comments, lines
  751–812), `auth/db.py:1–8` (single-process note),
  `deploy/thermal-printer.service` (gunicorn flags), `.github/workflows/ci.yml`
  (CI = pytest + auth smoke), `tests/conftest.py` (test env), `pytest.ini`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Activate venv | `source .venv/bin/activate` | prompt shows `(.venv)` |
| Sanity | `python -m pytest -q` | all pass (no code change in this plan) |

## Scope

**In scope**:
- `CLAUDE.md` (create, repo root)

**Out of scope** (do NOT touch):
- `README.md`, `DEPLOY.md` — they stay the human-facing docs; do not move
  content out of them or add "see CLAUDE.md" links.
- Any source file.

## Git workflow

- Branch: `advisor/004-claude-md`
- Single commit, e.g. `Docs: add CLAUDE.md with agent-facing constraints`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Create `CLAUDE.md` with exactly this content

(Adjust only if the drift check surfaced changes; note any adjustment in
your report.)

````markdown
# CLAUDE.md

Hobby project: a web GUI for an 80mm USB thermal receipt printer on a
Raspberry Pi, plus a public "send me a receipt" page for friends
(`/m/`, exposed via Tailscale Funnel). Tone of the codebase: lean and
playful, not enterprise. Prefer the smallest change that keeps the
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
  because nothing but the local Tailscale proxy can reach the port.
  Never bind 0.0.0.0, never set `DEV_BYPASS_TAILNET` or `FLASK_DEBUG` in
  anything deploy-shaped (the Werkzeug debugger on a funneled app is RCE).
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
````

**Verify**: `test -f CLAUDE.md && head -3 CLAUDE.md` → prints the heading.

### Step 2: Confirm nothing else changed

**Verify**: `git status --porcelain` → only `CLAUDE.md` (+
`plans/README.md` when you update the index) — and
`python -m pytest -q` → all pass.

## Test plan

No code change; the full suite run in Step 2 is the regression gate.

## Done criteria

- [ ] `CLAUDE.md` exists at repo root with the sections above
- [ ] `python -m pytest -q` exits 0
- [ ] `git status --porcelain` shows only `CLAUDE.md` (and `plans/README.md`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- A `CLAUDE.md` or `AGENTS.md` appeared since `2b723bc` — reconcile,
  don't overwrite.
- The drift check shows `config.py` / `app.py` / deploy files changed in
  ways that contradict a claim in the draft (e.g. worker count, port,
  env var names) — fix the draft to match reality and say so, or stop if
  the contradiction is substantial.

## Maintenance notes

- This file should stay under ~80 lines; it's a constraints sheet, not
  documentation. When a constraint stops being true (e.g. the queue moves
  to SQLite), update or delete its bullet in the same PR.
- Reviewer should check every factual claim against the code, not the
  plan — the file is only useful if it stays true.
