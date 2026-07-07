# Plan 006: Opt-in scheduled morning briefing (env-driven, off by default)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat <base>..HEAD -- app.py config.py README.md tests/`
> where `<base>` is the merge of plans 001–005 (branch `advisor/005-dependency-hygiene`
> at the time of writing). If the excerpts below don't match live code, STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW-MED
- **Depends on**: 001–005 merged (shares `app.py`; expected test counts assume them)
- **Category**: direction (owner-approved 2026-07-06)
- **Planned at**: `2b723bc` + branches `advisor/001`…`advisor/005`, 2026-07-06

## Why this matters

The morning-briefing widget (weather + HN + on-this-day) exists precisely
for mornings, yet printing it requires opening the console from the
tailnet and clicking a button. The owner approved automating it **as an
option**: off by default, opt-in via config. The repo's stated philosophy
is "everything is env-driven with sensible defaults" (README), so the
option is a single env var, not a scheduling UI.

Owner constraint (recorded 2026-07-06): anything automated must default
OFF. Also: no emoji in any UI copy or print template — the briefing
template already complies; keep it that way.

## Current state

- `features/widgets.py:538–577` — `morning_briefing_sections(location="")`
  returns a list of markup sections; empty location falls through to
  `config.DEFAULT_LOCATION` inside the weather widget.
- `app.py:357–370` — `print_briefing` route calls
  `_print_sections(widgets.morning_briefing_sections(location=loc))`.
  `_print_sections` (`app.py:115–133`) rasterizes each section and prints
  them as one scroll; it acquires the USB lock internally via
  `open_printer()`.
- `app.py:812` — precedent for a module-level daemon thread:
  `threading.Thread(target=_print_worker, name="friend-print-worker", daemon=True).start()`
- `app.py:1013–1021` — `_print_banner()` prints startup lines (dev token,
  DRY_RUN notice); the schedule notice belongs here too.
- `config.py` — env helpers `_env_bool` / `_env_int` at lines 14–30; all
  settings are module-level constants with why-comments.
- `README.md` — "Configuration" table lists every env var.
- `.env.example` — commented sample lines for each var.
- The systemd unit runs gunicorn `--workers 1` — exactly one scheduler
  thread will exist in prod. In local dev with `FLASK_DEBUG=1` the
  Werkzeug reloader runs the module twice; the existing print-worker
  thread already has that property, so it is accepted, not new risk.

Conventions: comments explain *why* in full sentences; threads are daemon
threads started at import; errors in background work are logged to stderr
with `traceback.print_exc()` and never kill the thread.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Full suite | `.venv/bin/python -m pytest -q` (or the venv-activated equivalent) | all pass; count = pre-plan count + 4 |
| Manual check | `BRIEFING_SCHEDULE=07:30 DRY_RUN=true DEV_BYPASS_TAILNET=true COOKIE_SECURE=false timeout 5 .venv/bin/python app.py 2>&1 \| head -20` | banner includes `scheduled briefing: 07:30` |

## Scope

**In scope**:
- `config.py` — one new setting
- `app.py` — schedule parsing helpers, scheduler thread, banner line
- `README.md` — one row in the Configuration table
- `.env.example` — one commented line
- `tests/test_schedule.py` (create)

**Out of scope** (do NOT touch):
- `features/widgets.py` — the briefing content is fine as is.
- Any schedule UI in `templates/` or `static/` — explicitly not wanted;
  the option is env-only.
- `deploy/` — no systemd timer; the scheduler is in-process.
- The friend queue / `_print_worker` — the briefing prints directly via
  `_print_sections`, same as the button does.

## Git workflow

- Branch: `advisor/006-scheduled-briefing` (branch off `advisor/005-…` if
  unmerged, otherwise main)
- Commit style: `Widgets: opt-in scheduled morning briefing (BRIEFING_SCHEDULE)`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Config

In `config.py`, after `DEFAULT_LOCATION`:

```python
# Opt-in daily auto-print of the morning briefing. Empty (the default)
# = off. Set to a 24h local time like "07:30" to have the briefing print
# itself every day at that time — location comes from DEFAULT_LOCATION.
# Validated at boot in app.py so a typo fails loudly instead of silently
# never printing.
BRIEFING_SCHEDULE = os.getenv("BRIEFING_SCHEDULE", "").strip()
```

**Verify**: `.venv/bin/python -c "import config; print(repr(config.BRIEFING_SCHEDULE))"` → `''`

### Step 2: Scheduler in `app.py`

Add below the friend-print worker block (after line ~812), following the
same module-level daemon-thread pattern:

```python
# ---------- scheduled briefing (opt-in) ----------

def _parse_schedule(value: str) -> "dt_time | None":
    """Parse BRIEFING_SCHEDULE ("HH:MM", 24h) or None when unset.
    Raises ValueError on garbage so a typo'd .env fails at boot, loudly,
    instead of never printing and never saying why."""

def _seconds_until(target: "dt_time", now: "datetime") -> float:
    """Seconds from `now` to the next occurrence of `target` (today if
    still ahead, else tomorrow). Pure function so tests don't sleep."""

def _briefing_scheduler(target: "dt_time") -> None:
    while True:
        # Re-check the clock at most every 60s rather than one long
        # sleep — robust to NTP jumps and suspend/resume on the Pi.
        remaining = _seconds_until(target, datetime.now())
        if remaining > 60:
            time.sleep(60)
            continue
        time.sleep(remaining)
        try:
            _print_sections(widgets.morning_briefing_sections())
        except Exception:
            # Never let a flaky widget or an offline printer kill the
            # scheduler — tomorrow is another morning.
            traceback.print_exc()
        # Skip past the target minute so we fire once per day.
        time.sleep(61)


_briefing_time = _parse_schedule(config.BRIEFING_SCHEDULE)
if _briefing_time is not None:
    threading.Thread(target=_briefing_scheduler, args=(_briefing_time,),
                     name="briefing-scheduler", daemon=True).start()
```

Implementation notes:
- Import what you need at the top of `app.py` in house style
  (`import time`, `from datetime import datetime, time as dt_time`) —
  check what's already imported first (`datetime` is not, as of the base).
- Fill in the two helper bodies; keep them pure (no I/O) — they are the
  unit-tested surface. `_parse_schedule("")` → `None`; `"7:30"` and
  `"07:30"` both valid; `"25:00"`, `"0730"`, `"seven"` → `ValueError`
  with a message naming `BRIEFING_SCHEDULE`.
- In `_print_banner()`, add:
  `if config.BRIEFING_SCHEDULE: print(f"Scheduled briefing: daily at {config.BRIEFING_SCHEDULE}")`

**Verify**: the manual-check command from the table shows the banner line;
running without `BRIEFING_SCHEDULE` shows no banner line and starts no
thread (add a quick check: `.venv/bin/python -c "import app; import threading; print([t.name for t in threading.enumerate()])"` → no `briefing-scheduler`).

### Step 3: Docs

- `README.md` Configuration table, after `DEFAULT_LOCATION`:
  `| BRIEFING_SCHEDULE | no | (off) | Set "HH:MM" (24h) to auto-print the morning briefing daily. Empty = off. |`
- `.env.example`: matching commented line in the file's existing voice.
  If your tooling refuses to open `.env.example` (some sandboxes block
  `.env*` patterns), skip it and flag in NOTES — do not work around.

**Verify**: `rg -n "BRIEFING_SCHEDULE" README.md .env.example config.py app.py` → present in all four (or three + a NOTES flag).

### Step 4: Tests — `tests/test_schedule.py` (new file)

Model header/docstring style on `tests/test_friend_queue.py`. Four tests,
no sleeping, no threads:

1. `test_parse_schedule_empty_is_none` — `app_module._parse_schedule("")`
   → `None`.
2. `test_parse_schedule_accepts_hhmm` — `"07:30"` and `"7:30"` →
   `dt_time(7, 30)`.
3. `test_parse_schedule_rejects_garbage` — each of `"0730"`, `"25:00"`,
   `"seven"`, `"07:30:00"` raises `ValueError` mentioning
   `BRIEFING_SCHEDULE`.
4. `test_seconds_until_wraps_to_tomorrow` — with
   `now = datetime(2026, 7, 6, 8, 0)` and target `dt_time(7, 30)`,
   result is `23.5 * 3600`; with target `dt_time(8, 30)`, result is
   `1800`.

**Verify**: `.venv/bin/python -m pytest tests/test_schedule.py -q` → 4 passed;
full suite → pre-plan count + 4.

## Test plan

Unit tests cover the schedule math (the only nontrivial logic). The
thread loop is deliberately thin glue over tested helpers +
already-tested `_print_sections`. Manual verification for the owner
(include in your report):

- **to test:** set `BRIEFING_SCHEDULE` to two minutes from now with
  `DRY_RUN=true`, run `python3 app.py`, confirm `data/last_print.bin`
  appears at that minute; then once on the Pi with real paper.

## Done criteria

- [ ] Full suite exits 0, +4 tests vs. pre-plan baseline
- [ ] `BRIEFING_SCHEDULE` unset → `threading.enumerate()` has no
      `briefing-scheduler` thread
- [ ] `BRIEFING_SCHEDULE=07:30` → banner line present
- [ ] `rg -n "BRIEFING_SCHEDULE" config.py app.py README.md` → all hit
- [ ] `git status --porcelain` limited to in-scope files
- [ ] `plans/README.md` status row updated (unless reviewer maintains it)

## STOP conditions

Stop and report back (do not improvise) if:

- `_print_sections` or the worker-thread block moved/changed vs. the
  excerpts (drift).
- You find an existing scheduling mechanism (env var, systemd timer in
  `deploy/`) — the plan assumes none exists.
- The import of `datetime` conflicts with an existing symbol in `app.py`
  in a way that forces renaming beyond the suggested aliases.
- Anything tempts you to add a UI toggle — that's explicitly out of scope.

## Maintenance notes

- If a second scheduled thing is ever wanted (e.g. weekly todo),
  generalize then — one env var per widget won't scale past two; a small
  `SCHEDULES="07:30=briefing,..."` grammar would. Deliberately not built
  now (YAGNI).
- DST: `_seconds_until` recomputes from wall-clock at most every 60s, so
  a DST jump shifts the fire time by at most a minute of drift — fine for
  a breakfast print. Do not "fix" with pytz/zoneinfo complexity.
- Reviewer: check the scheduler thread cannot start in pytest runs
  (conftest doesn't set `BRIEFING_SCHEDULE`, so it won't — keep it that
  way).
