# Plan 001: Make friend-print bookkeeping crash-safe (no in-flight leak, no stranded 'queued' rows)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 2b723bc..HEAD -- app.py auth/db.py tests/test_friend_queue.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `2b723bc`, 2026-07-06

## Why this matters

This is a personal Flask app on a Raspberry Pi that lets approved friends
print messages on a physical thermal receipt printer via a public page
(`/m/`, exposed through Tailscale Funnel). Friend prints go through an
in-memory FIFO queue with a per-user in-flight cap of 3.

Two bookkeeping paths can go wrong:

1. **In-flight cap leak.** `friend_print()` in `app.py` increments the
   per-user in-flight counter, *then* writes a history row to SQLite. If
   that DB write raises (disk hiccup, lock contention), the exception
   propagates — the counter is never decremented, and Flask returns an HTML
   500 page instead of the JSON error shape every other route uses. After 3
   such failures a friend is locked out ("you already have 3 prints
   queued") until the app restarts.

2. **Stranded 'queued' history rows.** The queue is in-memory. If the
   process restarts (deploy, crash, power cut) with jobs still queued, the
   corresponding `messages` rows stay `status='queued'` forever. The friend
   page auto-polls pending rows, so a friend sees a permanently "queued"
   message that will never print and never resolve.

## Current state

Relevant files:

- `app.py` — Flask entrypoint, all routes. Contains the friend print queue
  (`_PRINT_QUEUE`), the per-user in-flight dict (`_inflight` +
  `_inflight_lock` + `_dec_inflight`, lines 771–782), the queue worker
  (`_print_worker`, lines 785–812), and the `friend_print` route
  (lines 848–897).
- `auth/db.py` — SQLite schema + CRUD. `init()` (lines 73–94) runs
  idempotent schema creation + tiny forward migrations; it is called once
  at import time from `app.py:47` (`auth_db.init()`), *before* the worker
  thread starts (`app.py:812`) and before any request is served.
- `tests/test_friend_queue.py` — existing queue tests; the patterns to
  copy.

The buggy region, `app.py:866–897` as of commit `2b723bc`:

```python
    with _inflight_lock:
        if _inflight.get(user["id"], 0) >= _PER_USER_QUEUE_CAP:
            return jsonify({
                "ok": False,
                "error": f"you already have {_PER_USER_QUEUE_CAP} prints queued — "
                         "let them finish first",
                "kind": "user_cap",
            }), 429
        _inflight[user["id"]] = _inflight.get(user["id"], 0) + 1

    # Log to history at enqueue time so the friend sees their message
    # immediately, even before the worker actually pulls it. The row starts
    # 'queued'; the worker flips it to 'printed' or 'failed' so the friend
    # can see whether it actually hit paper.
    msg_id = auth_db.log_message(user["id"], body, status="queued")

    # qsize() before put = jobs the printer must finish first. Approximate
    # (the worker may have started one but not yet decremented), but close
    # enough for a UI hint.
    ahead = _PRINT_QUEUE.qsize()
    try:
        _PRINT_QUEUE.put_nowait((user["id"], msg_id, formatted))
    except queue.Full:
        _dec_inflight(user["id"])
        auth_db.delete_message(msg_id)  # never entered the queue
        return jsonify({
            "ok": False,
            "error": "the print queue is full — try again in a minute",
            "kind": "queue_full",
        }), 503

    return jsonify({"ok": True, "queued": True, "ahead": ahead})
```

Note the gap: `auth_db.log_message(...)` sits *between* the counter
increment and the `try`. Only `queue.Full` is handled; any other exception
leaks the increment.

`auth/db.py:73–94`, `init()` as of `2b723bc` (where the reconciliation
goes):

```python
def init() -> None:
    """Create tables on first run. Idempotent.
    ...
    """
    with db() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "name_style" not in cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN name_style TEXT NOT NULL DEFAULT 'plain'"
            )
        msg_cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        if "status" not in msg_cols:
            # Pre-status rows were logged synchronously at print time, so
            # 'printed' is the honest backfill.
            conn.execute(
                "ALTER TABLE messages ADD COLUMN status TEXT NOT NULL DEFAULT 'printed'"
            )
```

Repo conventions that apply here:

- Error responses are JSON `{"ok": False, "error": <msg>, "kind": <tag>}`.
  The `kind` values in use: `"printer"`, `"input"`, `"server"`,
  `"user_cap"`, `"queue_full"` (see `_safe()` at `app.py:136–149` and the
  route above). A generic internal failure uses `"kind": "server"` with
  HTTP 500 and `traceback.print_exc()` to stderr.
- Comments explain *why*, in full sentences, matching the voice of the
  excerpts above. Keep the existing comments; don't strip them.
- `app.py` imports at top: `import queue`, `import sys`, `import traceback`,
  `from auth import db as auth_db` — everything you need is already
  imported.
- Tests use module-level fixtures + a `_signed_in_client` helper; see
  `tests/test_friend_queue.py:15–25`. Tests have docstrings stating the
  contract being protected. `tests/conftest.py` forces `DRY_RUN=true`, a
  temp `DATA_DIR`, and `DEV_BYPASS_TAILNET=true` for every test.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Activate venv | `source .venv/bin/activate` | prompt shows `(.venv)` |
| Full test suite | `python -m pytest -q` | all pass (82 before this plan, 85 after) |
| One file | `python -m pytest tests/test_friend_queue.py -q` | all pass |
| Auth smoke | `DRY_RUN=true ADMIN_TOKEN=t DATA_DIR=/tmp/tp-smoke python scripts/test_auth_flow.py` | exits 0 |

## Scope

**In scope** (the only files you should modify):
- `app.py` — only the `friend_print()` route body (lines 848–897 region)
- `auth/db.py` — only the `init()` function
- `tests/test_friend_queue.py` — add three tests

**Out of scope** (do NOT touch, even though they look related):
- `_print_worker()` in `app.py` — it already handles print and DB failures
  correctly; do not restructure it.
- `printer.py`, `static/friends.js`, `templates/friends.html` — the
  frontend already auto-polls pending rows; no client change is needed.
- The response shapes and status codes of the existing success/429/503
  paths — `static/friends.js` matches on `kind`; changing shapes breaks it.
- Rate limiting in `auth/blueprint.py`.

## Git workflow

- Branch: `advisor/001-friend-print-bookkeeping`
- Single commit is fine. Message style matches the repo, a `Component:
  summary` first line, e.g. `Friend queue: crash-safe bookkeeping and
  orphaned-row reconciliation` (see `git log --oneline -10` for the voice).
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Guard the enqueue bookkeeping in `friend_print()`

In `app.py`, restructure the block quoted above so every failure path
after the in-flight increment undoes it. Target shape (keep the existing
comments where they still apply):

```python
    with _inflight_lock:
        if _inflight.get(user["id"], 0) >= _PER_USER_QUEUE_CAP:
            return jsonify({
                "ok": False,
                "error": f"you already have {_PER_USER_QUEUE_CAP} prints queued — "
                         "let them finish first",
                "kind": "user_cap",
            }), 429
        _inflight[user["id"]] = _inflight.get(user["id"], 0) + 1

    msg_id = None
    try:
        # Log to history at enqueue time so the friend sees their message
        # immediately, even before the worker actually pulls it. The row
        # starts 'queued'; the worker flips it to 'printed' or 'failed' so
        # the friend can see whether it actually hit paper.
        msg_id = auth_db.log_message(user["id"], body, status="queued")

        # qsize() before put = jobs the printer must finish first.
        # Approximate (the worker may have started one but not yet
        # decremented), but close enough for a UI hint.
        ahead = _PRINT_QUEUE.qsize()
        _PRINT_QUEUE.put_nowait((user["id"], msg_id, formatted))
    except queue.Full:
        _dec_inflight(user["id"])
        auth_db.delete_message(msg_id)  # never entered the queue
        return jsonify({
            "ok": False,
            "error": "the print queue is full — try again in a minute",
            "kind": "queue_full",
        }), 503
    except Exception:
        # Bookkeeping failed (most likely a SQLite hiccup). Undo the
        # in-flight increment so the friend isn't locked out of the cap
        # until the next restart, and answer in the JSON shape the page
        # expects instead of Flask's HTML 500.
        _dec_inflight(user["id"])
        if msg_id is not None:
            try:
                auth_db.delete_message(msg_id)
            except Exception:
                pass
        traceback.print_exc()
        return jsonify({"ok": False, "error": "internal error",
                        "kind": "server"}), 500

    return jsonify({"ok": True, "queued": True, "ahead": ahead})
```

**Verify**: `python -m pytest tests/test_friend_queue.py -q` → all existing
tests still pass (the `queue.Full` test at
`tests/test_friend_queue.py:76–92` exercises this exact block).

### Step 2: Reconcile orphaned 'queued' rows at boot

In `auth/db.py`, at the end of `init()` (inside the existing
`with db() as conn:` block, after the `status` column migration), add:

```python
        # The print queue lives in process memory, so any row still
        # 'queued' at boot belongs to a job that no longer exists — the
        # process restarted before the worker got to it. Flip them to
        # 'failed' so the friends page stops showing a print that will
        # never happen. Safe here: init() runs at import, before the
        # worker thread starts and before any request can enqueue.
        conn.execute("UPDATE messages SET status = 'failed' WHERE status = 'queued'")
```

**Verify**: `python -m pytest -q` → all pass (no existing test creates a
pre-boot queued row, so nothing should change yet).

### Step 3: Add three tests to `tests/test_friend_queue.py`

Model structure, fixtures, and docstring style on the existing tests in
that file (`test_friend_print_queue_full_rolls_back_inflight` is the
closest pattern). Use the existing `client` fixture and
`_signed_in_client` helper.

1. `test_friend_print_db_failure_rolls_back_inflight(client, monkeypatch)`
   — contract: a `log_message` crash returns JSON 500 and does not eat a
   cap slot.
   - Sign in a fresh user.
   - `monkeypatch.setattr(auth_db, "log_message", <function that raises
     sqlite3.OperationalError("boom")>)` (import `sqlite3` at top of the
     test file; `auth_db` is already imported there. Patching the
     `auth.db` module attribute also patches `app.py`'s view of it — both
     reference the same module object).
   - POST `/api/m/print` with a valid body → assert status 500, JSON
     `ok is False`, `kind == "server"`.
   - Assert `app_module._inflight.get(user["id"], 0) == 0` under
     `app_module._inflight_lock` (copy the locking pattern from
     `test_friend_print_queue_full_rolls_back_inflight`).
   - `monkeypatch.undo()`, then POST again with a valid body → assert 200
     and `queued is True` (proves the friend is not locked out), then
     `app_module._PRINT_QUEUE.join()` so the job drains before teardown.

2. `test_worker_survives_status_update_failure(client, monkeypatch)` —
   contract: a DB error while flipping `queued → printed` must not kill
   the worker thread; the next job still processes.
   - Sign in a fresh user.
   - Monkeypatch `auth_db.set_message_status` to raise
     `sqlite3.OperationalError("boom")`.
   - POST `/api/m/print` (→ 200), then `app_module._PRINT_QUEUE.join()`.
   - `monkeypatch.undo()`, POST a second message, `join()` again.
   - Assert via `auth_db.list_messages_for_user(...)` that the second
     message's row has `status == "printed"` (worker survived), and the
     user's in-flight count is 0 under the lock (both jobs decremented).

3. `test_init_reconciles_orphaned_queued_rows()` — contract: boot flips
   leftover 'queued' rows to 'failed'.
   - No client needed. Create a user via `auth_db.create_pending_user`,
     insert a row with `auth_db.log_message(user_id, "orphan",
     status="queued")`.
   - Call `auth_db.init()` directly (it is idempotent — that's documented
     in its docstring).
   - Assert the row's status is now `"failed"` via
     `auth_db.list_messages_for_user`.
   - Note: this test simulates a restart; it runs `init()` while the app's
     worker is idle, which matches the real boot ordering.

**Verify**: `python -m pytest tests/test_friend_queue.py -q` → all pass,
3 more tests than before.

## Test plan

Covered by Step 3 (the three new tests are the point of the plan). Also
run the full suite and the auth smoke to confirm no collateral damage:

- `python -m pytest -q` → 85 passed
- `DRY_RUN=true ADMIN_TOKEN=t DATA_DIR=/tmp/tp-smoke python scripts/test_auth_flow.py` → exits 0

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `python -m pytest -q` exits 0 with 85 passed
- [ ] `rg -n "except queue.Full" app.py` still returns exactly one match
      inside `friend_print`
- [ ] `rg -n "status = 'failed' WHERE status = 'queued'" auth/db.py`
      returns one match inside `init()`
- [ ] `git status --porcelain` shows changes only to `app.py`,
      `auth/db.py`, `tests/test_friend_queue.py` (and `plans/README.md`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The `friend_print` body at `app.py:848–897` does not match the "Current
  state" excerpt (drift since `2b723bc`).
- Any *pre-existing* test fails after Step 1 or Step 2 — the restructure
  must be behavior-preserving for the success, 429, and `queue.Full`
  paths.
- Test 2 turns out flaky (worker timing): do not add sleeps; report
  instead. `_PRINT_QUEUE.join()` is the intended synchronization — the
  worker calls `task_done()` only after the status update attempt.
- You find `init()` is called anywhere other than `app.py`'s import-time
  call and the idempotent-recall in your new test (check with
  `rg -n "auth_db.init|db.init\(\)" --type py .`) — the reconciliation's
  safety argument depends on boot ordering.

## Maintenance notes

- If the queue is ever made persistent (e.g. jobs survive restart), the
  Step 2 reconciliation becomes wrong — it assumes queued rows are orphans
  at boot. Whoever does that must remove it.
- If a second state-mutating step is ever added between the in-flight
  increment and the enqueue, it must go inside the same `try` block.
- Reviewer should scrutinize: no return path from `friend_print` after the
  increment can skip either `_dec_inflight` or a successful enqueue.
