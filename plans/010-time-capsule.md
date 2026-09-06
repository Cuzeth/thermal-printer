# 010 — Time capsules and optional quiet hours

Status: DONE (214 tests, auth smoke and JS syntax checks passed; awaiting commit).
Scope: friends' text, doodles and photo strips, including
saved-strip reprints. Owner prints and the daily briefing retain their behavior.

## Plan

1. Add nullable UTC `deliver_at` and `requested_for` columns to messages, plus
   `scheduled` and `cancelled` lifecycle states. Migrate in place. Use the saved
   row as the job so scheduling keeps anonymous choices, pixels and captions.
2. Validate explicit ISO dates with a UTC offset: strictly future, at most 365
   days away. Enforce 10 outstanding capsules per friend and 200 globally inside
   a SQLite write transaction, bounding retained images without extra services.
3. Claim due rows with a conditional update and feed the existing FIFO queue
   every 15 seconds, preserving its 3-per-friend and 50-job caps. Retry queue
   pressure on the next tick. Catch up overdue rows after downtime. Cancel only
   while `scheduled`; cancellation and dispatch compete atomically. Recheck
   blocked/deleted senders at claim and worker dispatch.
4. Add opt-in `FRIEND_QUIET_START`, `FRIEND_QUIET_END` and
   `FRIEND_QUIET_TIMEZONE` configuration (default America/Phoenix). Both empty
   means off. Recheck quiet hours at worker dispatch, including restarted jobs.
   Start is inclusive, end exclusive; overnight and same-day windows work.
   Advance in real UTC minutes to find the next permitted wall time, so missing
   spring-forward minutes are skipped and repeated autumn minutes obey the
   configured wall-clock rule. The usual physical-print crash ambiguity remains:
   a crash during USB delivery may replay; printer failures become failed for
   explicit retry rather than unbounded automatic reprinting.
5. Add shared print-now/time-capsule controls above the three composer modes.
   Display the browser timezone and chosen instant explicitly, convert to UTC
   before sending, and show effective server delivery dates in history. Keep
   cancel a separate native button outside the row's restore interaction. Use
   existing plain CSS tokens, visible states, 44px controls and tabular dates.
6. Test time boundaries, UTC validation, migrations/restart, all three content
   kinds, saved photos, cap enforcement, claim/cancellation races, blocked users,
   quiet hours, queue pressure and printer failure. Run full pytest, auth smoke
   in DRY_RUN, and JS syntax checks. Update README, env example and plan index.

## Implemented behavior and limits

- Explicit dates and quiet-hour holds share the existing saved message payload,
  including photo captions and anonymous choices. Pending capsules are pinned
  in history so newer prints cannot hide cancellation.
- Due dispatch and cancellation use conditional SQLite updates. A full or broken
  queue releases the claim for the next tick. Recovered claimed capsules re-enter
  the due dispatcher, where the existing in-flight caps apply.
- Worker skips, quiet-hour deferrals and exceptions all release their accounting
  in `finally`. Revoking approval cancels waiting/claimed capsules; deletion
  cascades. Printer failures keep the existing failed/owner-retry lifecycle.
- Pending submission limits are durable: 10 per friend, 200 globally, including
  claimed jobs. Jobs already accepted before a quiet-hours boundary are retained
  even if deferral temporarily exceeds that cap. Existing history retention and
  the unavoidable physical-print crash/replay tradeoff are unchanged.
- No scheduler thread runs under pytest: tests call individual ticks with a fixed
  clock to avoid races while existing migration tests replace `DB_PATH`.

## Verification

- `.venv/bin/python -m pytest -q`: **214 passed**, including 47 new capsule cases.
  Three existing libusb0 deprecation warnings remain.
- `DRY_RUN=true ADMIN_TOKEN=t DATA_DIR=/tmp/tp-time-capsule-smoke .venv/bin/python scripts/test_auth_flow.py`:
  **ALL GREEN**.
- Bundled Node `--check` on `static/friends.js`, `static/photo.js`, and
  `static/app.js`: passed. `git diff --check`: passed.
- New tests cover UTC input validation; overnight, same-day and DST boundaries;
  schema migration; future/overdue restart behavior; real DRY_RUN delivery of
  text/doodles/photos; saved-photo ownership; scoped cancellation and concurrent
  claim races; concurrent cap reservations; queue pressure/errors; account
  revocation/deletion; worker-time quiet checks; owner/briefing isolation; printer
  failure; scheduler tick recovery; and pending-history discoverability.

## Interface review

Full review of the new delivery controls and waiting-history states using the
existing vanilla JS/plain CSS system. Source inspection only; no browser
automation or physical prints, as required by CLAUDE.md.

| Category | Evidence inspected | Result |
| --- | --- | --- |
| Typography | Explicit timezone/date hints; tabular input/history dates | Clear |
| Surfaces | Composer insets; native 44px inputs/cancel button; mobile single-column rule | Clear |
| Animations | Picker/status updates are immediate; existing print feedback retained | Clear; no new motion |
| Icons | No new icons or emoji | Clear |
| Performance | Existing preview flow; 15s history polling only while jobs are pending, preserving focused rows | Clear |

| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| MEDIUM | `templates/friends.html`, `static/friends.css` | Delivery timing unavailable | Shared labeled timing controls with composer insets and 44px targets | Clear state and comfortable touch targets |
| MEDIUM | `static/friends.js` | History rows only restore content | Waiting rows show dates and one native cancel action | Cancel cannot trigger a restore/reprint handler |
| LOW | `static/friends.js`, `static/photo.js` | Send buttons always say print | Capsule buttons say save; successful sends reset timing to now | Honest action and visible feedback |

Considered and rejected: an animated date-picker reveal (routine state change
needs no motion); a nested cancel button on a clickable history row (conflicting
keyboard/restore actions); separate timing controls in every mode (unnecessary
duplication and inconsistent choices).

Verdict: **Approve** for source review. **Not verified:** browser layout, mobile
picker rendering and keyboard interactions, and physical receipt quality.

## Remaining manual checks

1. On phone and desktop, switch print-now/capsule, choose a future local time,
   confirm its displayed timezone, and send text, a doodle and a photo strip.
   Verify the choice resets after success and remains after an input error.
2. Schedule a saved photo strip, cancel it with both mouse and keyboard, and
   verify the editor does not open. Check waiting/queued/printed history updates.
3. On a DRY_RUN instance, restart with a future and overdue capsule; then try a
   short quiet-hours window and verify queue entry before/after its boundary.
4. On the real printer, verify one due receipt of each kind, quiet-hour holding,
   and owner retry after a disconnected-printer failure. No hardware constants
   or raster chunk sizes were changed.
