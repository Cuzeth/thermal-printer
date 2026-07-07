# Plan 003: Cover the admin user-lifecycle endpoints with tests

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 2b723bc..HEAD -- app.py tests/test_routes.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (but if Plan 002 runs first, expected test counts shift by +4)
- **Category**: tests
- **Planned at**: commit `2b723bc`, 2026-07-06

## Why this matters

Approve / revoke / delete are the only way friends get in or out of this
app — they are the admin core of the friends feature. Today they have
**zero test coverage**: `tests/test_routes.py` covers the admin password
reset (3 tests) and the users list (1 test), but nothing exercises
`/approve`, `/revoke`, `/delete`, or `/api/admin/messages`. A refactor
that, say, flips `revoke` to set the wrong status, breaks the
delete-cascade, or drops an auth decorator would pass CI silently. These
endpoints change data and gate access, which makes them exactly the kind
of code this repo's test suite exists to pin down. This is a test-only
plan: no production code changes.

## Current state

Relevant files:

- `app.py` — the admin routes, lines 922–1003. All follow the same shape;
  here are the three under test (`app.py:934–961` as of `2b723bc`):

```python
@app.post("/api/admin/users/<int:user_id>/approve")
@require_tailnet
@require_admin
def admin_approve_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "no such user"}), 404
    auth_db.set_status(user_id, "allowed")
    return jsonify({"ok": True})


@app.post("/api/admin/users/<int:user_id>/revoke")
@require_tailnet
@require_admin
def admin_revoke_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "no such user"}), 404
    auth_db.set_status(user_id, "blocked")
    return jsonify({"ok": True})


@app.post("/api/admin/users/<int:user_id>/delete")
@require_tailnet
@require_admin
def admin_delete_user(user_id: int):
    if not auth_db.get_user(user_id):
        return jsonify({"ok": False, "error": "no such user"}), 404
    auth_db.delete_user(user_id)
    return jsonify({"ok": True})
```

  and `/api/admin/messages` (`app.py:982–990`): returns
  `{"ok": True, "messages": [...]}` where each row has
  `id, body, status, printed_at, username` (see
  `auth/db.py:220–229`, `list_messages()` — it JOINs users).

- `auth/db.py` — helpers the tests will use directly:
  `create_pending_user`, `get_user`, `set_status` (sets `approved_at` when
  status becomes `"allowed"`, see lines 160–170), `log_message`,
  `list_messages_for_user`. The `messages` table has
  `ON DELETE CASCADE` on `user_id` (line 38) — deleting a user deletes
  their messages.

- `tests/test_routes.py` — where the new tests go. Fixtures at the top
  (lines 12–19):

```python
@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {config.ADMIN_TOKEN}"}
```

  The exemplar to copy for structure, naming, and docstring voice is
  `test_admin_password_reset_flow` (lines 205–223): create a user via
  `auth_db`, hit the admin endpoint with `headers=auth`, then assert both
  the HTTP response and the DB/behavioral consequence.

Conventions:
- Test usernames are unique per test and prefixed by the topic (e.g.
  `pwreset_alice`, `hist_bob`) because tests share one SQLite file per
  session (`tests/conftest.py` sets a tmp `DATA_DIR` once). Use a fresh
  prefix like `adm_`.
- Passwords in tests are throwaway literals like `"hunter2hunter"` —
  fine to reuse; they're test fixtures, not secrets.
- Each test carries a one-or-two-line docstring stating the contract.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Activate venv | `source .venv/bin/activate` | prompt shows `(.venv)` |
| Just this file | `python -m pytest tests/test_routes.py -q` | all pass |
| Full suite | `python -m pytest -q` | all pass (82 before, 88 after this plan alone) |

## Scope

**In scope** (the only file you should modify):
- `tests/test_routes.py`

**Out of scope** (do NOT touch):
- `app.py`, `auth/db.py` — if a test you write fails against current
  behavior, that's a STOP condition (you may have found a real bug —
  report it), not a license to change production code.
- `tests/test_security.py` — tailnet-gate coverage lives there and is
  already adequate.

## Git workflow

- Branch: `advisor/003-admin-endpoint-tests`
- Single commit, repo style, e.g. `Tests: cover admin approve/revoke/delete
  and messages list`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Lifecycle tests

Append to `tests/test_routes.py`, after the existing admin tests
(~line 241), six tests:

1. `test_admin_approve_flow(client, auth)` — "Approve flips a pending user
   to allowed and stamps approved_at."
   - `user = auth_db.create_pending_user("adm_approve", "hunter2hunter")`
   - POST `f"/api/admin/users/{user['id']}/approve"`, `headers=auth` → 200,
     `ok is True`.
   - `fresh = auth_db.get_user(user["id"])` → `fresh["status"] == "allowed"`
     and `fresh["approved_at"] is not None`.

2. `test_admin_revoke_blocks_access(client, auth)` — "Revoke sets status
   'blocked' and a blocked friend's session stops working (403 on /m/)."
   - Create + approve a user (`set_status(..., "allowed")` via the API or
     helper — use the approve endpoint since it's now under test).
   - POST `.../revoke` → 200; `get_user` → `status == "blocked"`.
   - Sign their session in with the `client.session_transaction()` pattern
     from `test_friend_history_rejects_pending_user` (lines 85–95), then
     GET `/api/m/history` → 403.

3. `test_admin_delete_removes_user_and_history(client, auth)` — "Delete
   removes the user row and cascades to their messages."
   - Create user; `auth_db.log_message(user["id"], "doomed")`.
   - POST `.../delete` → 200.
   - `auth_db.get_user(user["id"])` → `None`;
     `auth_db.list_messages_for_user(user["id"])` → `[]` (the cascade).

4. `test_admin_lifecycle_404_on_missing_user(client, auth)` — "All three
   lifecycle endpoints 404 cleanly on an unknown id." Loop over
   `("approve", "revoke", "delete")`, POST
   `f"/api/admin/users/999999/{action}"` → 404 and
   `r.get_json()["error"] == "no such user"`.

5. `test_admin_lifecycle_requires_bearer(client)` — "No token → 401, and
   nothing changes." Create a pending user, POST all three endpoints
   without headers → 401 each; then `get_user` → status still
   `"pending"` (approve/revoke didn't fire) and the row still exists
   (delete didn't fire).

6. `test_admin_messages_lists_all_users_newest_first(client, auth)` —
   "Admin messages feed spans users, includes usernames, honors limit."
   - Two users (`adm_msgs_a`, `adm_msgs_b`), log one message each.
   - GET `/api/admin/messages`, `headers=auth` → 200; both bodies present;
     every row has keys `{"id", "body", "status", "printed_at",
     "username"}`.
   - GET `/api/admin/messages?limit=1` → exactly 1 row.

**Verify**: `python -m pytest tests/test_routes.py -q` → all pass.

### Step 2: Full-suite check

**Verify**: `python -m pytest -q` → exits 0, 6 more tests than before this
plan started (82 → 88 if run on `2b723bc`; add Plan 001/002's counts if
those landed first).

## Test plan

This plan *is* tests. Cases covered: happy path per endpoint, the 404
branch, the auth gate, the delete cascade, and the cross-user admin feed —
mirrors what the endpoints can actually do.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `python -m pytest -q` exits 0
- [ ] `rg -c "def test_admin_" tests/test_routes.py` reports ≥ 9 (3 existing + 6 new)
- [ ] `git status --porcelain` shows changes only to `tests/test_routes.py`
      (and `plans/README.md`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- Any new test fails against unmodified production code — that is a real
  finding; report the failing assertion and the observed behavior instead
  of adapting the test to pass.
- The route excerpts above don't match `app.py` (drift since `2b723bc`).
- You need to modify anything outside `tests/test_routes.py`.

## Maintenance notes

- If a future change adds an "unblock" endpoint distinct from approve (the
  UI currently re-uses approve for unblocking), extend
  `test_admin_revoke_blocks_access` to cover the round-trip.
- These tests intentionally reach into `auth_db` for setup/assertions —
  that matches house style (`test_admin_password_reset_flow`), keep it
  that way rather than introducing API-only setup helpers.
