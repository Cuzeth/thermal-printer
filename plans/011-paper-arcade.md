# 011 — Paper Arcade

Status: DONE (316 tests + auth smoke + JS syntax checks; parent review passed)

## Scope and implementation plan

Build one owner-console tab for Sudoku, mazes and word searches. Each has a
deliberate **new puzzle** action, an exact receipt preview and **print puzzle**.
Changing kind clears the selection; printing requires the selected identifier
and never chooses a new puzzle. No friend games, scheduling, external services,
dependencies, difficulty picker or custom-word editor.

1. Add a bounded, versioned local generator and raster renderer in
   `features/arcade.py`. Sudoku uses validity-preserving random transformations
   of a fixed, verified unique puzzle. Maze uses a spanning-tree backtracker.
   Word search places eight fixed-theme words with bounded candidate searches.
2. Sign a short version/kind/seed/width identifier with the persistent Flask
   `SECRET_KEY` and a dedicated signing salt. A deterministic hash-based random
   stream preserves puzzle identities across Python and process restarts.
   Include a public-base-URL fingerprint to reject stale print settings.
3. Keep new/preview/print APIs under `@require_admin` and `_safe()`. Add exactly
   one deliberately public, read-only `/arcade/solution/<token>` route. Verify
   bounded signed payloads before generating. This exposes generated puzzle
   solutions only, with no friend data, account operations or print actions.
4. Render the entire receipt including its solution QR once per request using
   the same path for preview and print. QR modules use integer sizing and a
   four-module quiet zone. Honor 384/576 px hardware width and existing safe
   `_print_image` fragmentation; leave `features/render.py` constants alone.
5. Add controls and loading/empty/error/ready states using existing vanilla JS,
   CSS tokens, keyboard tab behavior and shared preview panel. Retain selected
   puzzles when switching console tabs. Add a responsive public solution page
   with readable puzzle solution images and equivalent text where practical.
6. Test Sudoku uniqueness, maze connectivity, word placement, determinism,
   validation/CPU bounds, signature/auth boundaries, preview/print pixel parity,
   QR content/quiet zones and printer helper behavior. Run full pytest, DRY_RUN
   auth smoke and JS syntax checks; render/inspect puzzle and solution PNGs.
7. Document production `PUBLIC_BASE_URL`, the loopback DRY_RUN/dev fallback,
   `SECRET_KEY` persistence, the narrow public route exception and hardware QA.

## Interface review

Apply `make-interfaces-feel-better` in the existing plain CSS system. Inspect
typography, structural surfaces, focus/disabled/loading states, restrained
motion and image scaling. No browser automation or new animation library.

## Verification results

- Full `.venv/bin/python -m pytest -q`: **316 passed** (214 baseline + 102
  Arcade cases). Three existing PyUSB/Python 3.14 deprecation warnings remain.
- `DRY_RUN=true ADMIN_TOKEN=t DATA_DIR=/tmp/tp-arcade-auth-smoke
  .venv/bin/python scripts/test_auth_flow.py`: **ALL GREEN**.
- Bundled Node `--check` on `static/app.js`, `static/friends.js` and
  `static/photo.js`: passed. `git diff --check`: passed.
- Ten independent Sudoku uniqueness searches pass; ten maze seeds satisfy
  reciprocal walls, full connectivity, tree edge count and valid solution paths;
  ten word-search seeds spell every listed word at its solution coordinates.
  A forced candidate-exhaustion test verifies the bounded fallback.
- Version 1 golden fixtures pin all three generators and their solutions.
  Signing checks cover malformed and correctly signed invalid payloads, unknown
  versions, wrong keys, input lengths, fixed widths and seed formats. Fresh signer
  instances reconstruct identical identifiers with a persistent key.
- Admin gate checks cover create/preview/print, including friend sessions.
  Public scans only render valid signed solutions; invalid links cannot reach
  generation or the printer. Settings changes reject stale previews for print.
- Pixel parity checks at both widths prove preview, refresh and print retain the
  same puzzle and QR. The print route calls the existing `_print_image` helper and
  footer, and a real DRY_RUN request emitted raster and cut ESC/POS commands.
  QR checks verify the full solution URL, four-module white margins and solid
  integer-sized modules; forged Host/forwarding headers do not affect that URL.
- Visually inspected `/tmp/tp-arcade-artifacts/contact-384.png` and
  `/tmp/tp-arcade-artifacts/contact-576.png`: all three puzzles and solutions have
  clean grids, visible clues, writing space, readable lists and QR margins.
  Individual PNGs are in the same directory as
  `{sudoku,maze,wordsearch}-{384,576}-{puzzle,solution}.png`.
- Parent independently reviewed both artifact sheets, routes, JS/CSS and ran
  the full 316-test suite. No blocking findings remained.

### Interface review result

Full review scoped to the new Arcade tab/shared receipt panel and public solution
page, in vanilla JS/plain CSS. Source state paths were inspected; no browser
automation or interactive browser verification was performed.

| Category | Evidence inspected | Result |
| --- | --- | --- |
| Typography | Arcade controls, rendered 384/576 px puzzle/solution PNGs, textual solutions | Clear; existing font loader reused with `mono_regular`, new grid sizes only |
| Surfaces | Existing CSS tokens, shared details styles, light solution page, image outlines | Clear after local token/margin/counter overrides |
| Animations | New-puzzle state transitions, shared preview animation selector | Repeated puzzle changes skip the entrance animation; no new motion |
| Icons | Existing numbered console tabs and details affordances | Existing convention retained; no new icon library or emoji |
| Performance | Generation loops, request limits, preview selection/in-flight guards | Fixed-size generation and compact signed identifiers; controls prevent duplicate in-flight actions |

| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| MEDIUM | `features/arcade.py` text helper | Initial regular-font key was not in the existing loader | Use `mono_regular` | Correct font routing keeps receipt instructions readable |
| MEDIUM | `static/style.css` solution details | Shared child margins and lab counter leaked into new page | Reset child inline margins and suppress counter locally | Prevent table overflow and unrelated labels |
| LOW | `static/style.css` solution page | Dark semantic tokens remained on a light ground | Set light text, surface, selection and line tokens | Keep focus, selection and markers legible |
| LOW | `static/style.css` Arcade preview | Shared image entrance replayed for each generated puzzle | Disable it only while Arcade is active | Frequent generation needs stable grids |
| LOW | `static/style.css` shared input selector | Existing capsule datetime picker missed shared input styling | Include `datetime-local` | Parent-requested one-line integration fix keeps the date control consistent |

Considered and rejected: adding a new animation package (existing styles suffice),
restyling all shared details (local overrides keep this change scoped), and
retuning rich-text rendering constants (hardware-validated and outside scope).

Verdict: **Approve** for inspected source/artifacts. Manual mobile/keyboard
interaction and physical printer/phone scanning remain unverified below.

## Real-printer checks

- On the real configured paper width, generate and print each puzzle. Confirm
  thin maze walls, Sudoku cells and word letters remain clear and comfortable to
  mark with a pencil; confirm there is one clean cut and no buffer corruption.
- Scan each printed QR on a phone while signed out. Match the receipt number,
  check the maze dots/word coordinates, and rescan after an app restart with the
  same `SECRET_KEY`. Production defaults to the existing working hostname.
- Manually check new/print busy states, keyboard navigation, tab switching,
  failure/retry behavior, and the solution page on a narrow phone screen.

No USB printing, dependency changes, commits, pushes or deployments were performed
by the implementation agent. The parent reviewed this work for the third sequential feature commit.
