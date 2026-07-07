# Plan 008: "printer looks offline" banner on the friends page

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat <base>..HEAD -- printer.py app.py static/friends.js templates/friends.html static/friends.css tests/`
> where `<base>` is the tip of `advisor/005-dependency-hygiene` (or main if
> merged). On excerpt mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 001–005 merged (shares `app.py` and test files)
- **Category**: direction (owner-approved 2026-07-06)
- **Planned at**: `2b723bc` + branches `advisor/001`…`advisor/005`, 2026-07-06

## Why this matters

Friends currently learn a print failed only after the fact: the history
row flips to "didn't print". The server already knows when the printer is
unreachable — every USB open failure funnels through `PrinterError` in
`printer.py`. Surfacing a soft "printer looks offline right now" banner on
`/m/` sets expectations *before* a friend composes a message. Owner
approved this on 2026-07-06.

Design rule from the plans index: **a stale flag is worse than none** — so
the flag is only ever set by a real failed open and cleared by any
successful print, and the UI copy says "looks offline", not "is offline".
(Owner constraint: no emoji in UI copy.)

## Current state

- `printer.py:95–166` — `open_printer()` context manager. All offline
  failures raise `PrinterError` (open-time recovery block, lines 132–154,
  and the around-yield mapping, lines 156–161). A successful print exits
  the `try/finally` at 162–166 normally. `config.DRY_RUN` short-circuits
  at lines 110–117 and never touches USB.
- `app.py` — friend routes live around lines 815–917; `_safe()` shape and
  `require_allowed` decorator conventions as in plans 001–003.
- `static/friends.js:72–97` — `applyMe(user)` is the state switchboard;
  the `allowed` branch already kicks off `loadHistory()`. The
  pending-state poller (`setPendingPolling`, lines 51–57) is the pattern
  for a lightweight interval.
- `templates/friends.html:146–197` — the `allowed` card: `<h1>` then a
  `.dim` paragraph, then settings/details, then `#msg-form`.
- `tests/test_routes.py` — `test_friend_print_queues_even_when_printer_offline`
  (the `_Offline` monkeypatch class) is the exemplar for simulating an
  unplugged printer.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Full suite | `.venv/bin/python -m pytest -q` | all pass; +3 vs. pre-plan |
| Single file | `.venv/bin/python -m pytest tests/test_printer.py tests/test_routes.py -q` | all pass |

## Scope

**In scope**:
- `printer.py` — tiny state tracker + `status()` accessor
- `app.py` — one GET route `/api/m/printer`
- `static/friends.js`, `templates/friends.html`, `static/friends.css` —
  banner + fetch wiring
- `tests/test_printer.py`, `tests/test_routes.py` — add tests

**Out of scope** (do NOT touch):
- The queue/worker — prints must still queue while offline (that behavior
  is tested and wanted; the banner only *informs*).
- `reset_device()` — reset success is not proof the printer prints;
  don't clear the flag there.
- The main console (`static/app.js`) — owner sees errors directly; this
  plan is friends-page only.

## Git workflow

- Branch: `advisor/008-printer-offline-banner`
- Commit style: `Friends page: printer-offline banner backed by real open failures`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Track last-known printer state in `printer.py`

Add near `_lock` (module level):

```python
# Last-known printer reachability, for the friends page's soft banner.
# Only a real failed open sets it False; only a completed print sets it
# True — never guessed, so it can't go stale in the scary direction.
# DRY_RUN never flips it (there's no printer to be offline).
_status_lock = threading.Lock()
_last_ok: bool = True
_last_change: float | None = None


def _mark(ok: bool) -> None:
    global _last_ok, _last_change
    with _status_lock:
        if _last_ok != ok:
            _last_change = time.time()
        _last_ok = ok


def status() -> dict:
    with _status_lock:
        return {"ok": _last_ok, "since": _last_change}
```

Wire `_mark()` into `open_printer()` (skip entirely in the DRY_RUN
branch):
- every `raise PrinterError(...)` path for offline-looking failures →
  `_mark(False)` immediately before the raise (three sites: recovery
  failed, device absent, around-yield offline mapping);
- the `finally` that closes after a successful yield → `_mark(True)` only
  when no exception occurred (restructure minimally: set a local
  `ok = True` after the `yield p` line inside the `try`, and in
  `finally` call `_mark(True)` only if `ok` — do not change the existing
  close/error semantics).

**Verify**: `.venv/bin/python -m pytest tests/test_printer.py -q` → existing
tests pass.

### Step 2: Friend-visible endpoint in `app.py`

Next to `friend_history` (~line 900), matching its docstring style:

```python
@app.get("/api/m/printer")
@require_allowed
def friend_printer_status():
    """Last-known printer reachability for the soft banner on /m/.

    Deliberately coarse: True until a real USB open fails, False until a
    print completes. Queueing is unaffected — the banner only sets
    expectations."""
    return jsonify({"ok": True, "printer": printer_status()})
```

Import: `printer.py` symbols are imported at `app.py:31` (`from printer
import PrinterError, footer, open_printer, ...`) — add `status as
printer_status` to that import.

**Verify**: `.venv/bin/python -m pytest tests/test_routes.py -q` → passes.

### Step 3: Banner on the friends page

- `templates/friends.html` — inside the `allowed` card, directly under the
  `<p class="dim">` intro (line ~148):
  `<div class="printer-banner" id="printer-banner" hidden>heads up — the printer looks offline right now. your receipt will queue and print once it's back.</div>`
- `static/friends.css` — a `.printer-banner` style consistent with the
  page's existing card/badge palette (amber-ish, small, rounded; look at
  `.history-status.failed` and `.toast.err` for the palette to echo).
- `static/friends.js`:
  - `async function refreshPrinterBanner()` — `getJSON("/api/m/printer")`,
    set `$("#printer-banner").hidden = j.printer?.ok !== false;`
    swallow errors (banner is best-effort; never toast from it).
  - Call it (fire-and-forget with `.catch(() => {})`) from the `allowed`
    branch of `applyMe` next to `loadHistory()`, and at the end of
    `sendMessage()` next to the history refresh.
  - Poll every 60s while in the allowed state: mirror the
    `setPendingPolling` pattern (lines 51–57) with a
    `setPrinterPolling(on)` helper — on in the `allowed` branch, off in
    every other `applyMe` outcome.

**Verify**: `rg -n "printer-banner|refreshPrinterBanner|setPrinterPolling" static/friends.js templates/friends.html static/friends.css` → all wired.

### Step 4: Tests

1. In `tests/test_printer.py` — reuse what's already there: the
   `live_mode` fixture (lines 35–38, flips `DRY_RUN` off) and
   `_FakeEagerFail` (lines 13–20).
   `test_status_flips_on_offline_open_and_recovers(live_mode, monkeypatch)`:
   - `monkeypatch.setattr(printer, "Usb", _FakeEagerFail)`, then
     `with pytest.raises(printer.PrinterError): with printer.open_printer(): pass`
     (same shape as `test_eager_open_failure_raises_printer_error`) →
     assert `printer.status()["ok"] is False`.
   - Then swap to a working fake (`open()`/`close()` no-op — model on the
     `_RandomFail` class inside
     `test_non_device_exception_is_not_masked`, lines 60–63), run
     `with printer.open_printer(): pass` → assert
     `printer.status()["ok"] is True`.
   - Note: `_FakeEagerFail` triggers the recovery path, which calls the
     real `usb.core.find` (no device on dev machines → returns None →
     clean PrinterError). The existing tests already rely on this;
     nothing new to mock.
2. In `tests/test_routes.py`:
   - `test_friend_printer_status_requires_session(client)` → GET
     `/api/m/printer` → 401.
   - `test_friend_printer_status_shape(client)` → signed-in allowed user
     (copy the `session_transaction` pattern) → 200, JSON has
     `printer.ok` as a bool.

**Verify**: `.venv/bin/python -m pytest -q` → all pass, +3 vs. pre-plan.

## Test plan

Covered in Step 4. Manual check for the owner (include in your report):

- **to test:** on the Pi, unplug the printer USB, send a friend message
  (it should queue + fail), reload `/m/` — banner appears; plug back in,
  print anything — banner clears within a minute.

## Done criteria

- [ ] Full suite exits 0, +3 tests vs. pre-plan baseline
- [ ] `rg -n "def status" printer.py` → one match; DRY_RUN path contains
      no `_mark` call
- [ ] `rg -n "api/m/printer" app.py static/friends.js` → both hit
- [ ] Banner copy contains no emoji
- [ ] `git status --porcelain` limited to in-scope files
- [ ] `plans/README.md` status row updated (unless reviewer maintains it)

## STOP conditions

Stop and report back (do not improvise) if:

- `open_printer()`'s structure doesn't match the excerpt line ranges
  (drift) — the `_mark` wiring depends on exact raise sites.
- Wiring `_mark(True)` cleanly requires restructuring the
  `try/except/finally` beyond adding a local flag — report instead of
  refactoring the error handling.
- Any existing `tests/test_printer.py` test starts failing.

## Maintenance notes

- If Plan 006 (scheduled briefing) lands, its prints also flow through
  `open_printer()` and will keep the flag fresh for free — no interaction
  needed.
- If a "printer status" indicator is ever wanted on the owner console,
  reuse `printer.status()`; don't invent a second tracker.
- Reviewer: confirm the banner never blocks sending (it's informational;
  the form stays enabled).
