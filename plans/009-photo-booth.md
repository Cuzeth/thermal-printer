# 009 — Friend photo booth

Status: DONE (implemented and validated; awaiting parent review and commit)

## Plan

1. Add a photo mode to the approved friends composer using the existing vanilla
   JavaScript and plain CSS. Accept one to four browser-readable photos; select a
   frame, adjust square crop position and zoom, remove or reorder frames, select
   soft / contrast / ink treatments, and add a 160-character caption.
2. Crop and resize in the browser before uploading, reducing phone-photo memory
   and transfer costs on the Pi. Independently validate image bytes, formats,
   frame count, dimensions, treatment, caption and total request size on the
   server. Normalize EXIF orientation and transparency, and reuse the existing
   image processing pipeline for monochrome output.
3. Use one rendering path for preview and printing. Store the finished strip as
   a normalized PNG with an explicit photo message kind, and enqueue using the
   existing shared bookkeeping. Preserve the friend name/anonymous header,
   timestamp footer, restart replay, owner retry, and fragment-safe printing.
4. Add an idempotent message-kind migration and expose that kind in history.
   Reopen photo history in the photo mode as a saved strip that can be previewed
   and explicitly reprinted; new uploads replace it for crop/caption editing.
   Original photos are not stored. History media remains scoped to its friend.
5. Cover processing, auth, malformed input, queue caps/unwind, migration,
   preview/print parity, history scoping and durable retry/replay with meaningful
   tests. Run the full pytest suite and DRY_RUN auth smoke; generate and inspect
   a DRY_RUN photo preview. Document usage, limits and real-printer checks.

## Boundaries

No new framework or dependencies, emoji, renderer constant retuning, browser
automation, USB printing, deployment, commits, or changes to unrelated features.
The parent reviews and commits this feature before the next fresh agent starts.

## Interface review

Using make-interfaces-feel-better in full scope for the photo composer, within
the project's existing CSS variables, square-edged surfaces and typography.
Review controls, empty/loading/error/ready/saved states, touch hit areas, focus,
preview staleness and reduced motion by source inspection. Browser interaction
is explicitly not verified because CLAUDE.md prohibits browser automation.

## Implementation and validation

Implemented in `features/photo.py`, `static/photo.js` and the existing friend
routes, template, CSS and database helpers. The message kind migration preserves
existing text and doodle rows. New photo submissions save a normalized 1-bit PNG
and descriptive history label; saved-strip preview and reprint validate owner
and photo kind before using those pixels. All printing still flows through
`printer.print_image` with the existing fragment-height workaround.

- `.venv/bin/python -m pytest -q`: **167 passed**, including 17 new photo cases.
  The three existing Python 3.14/libusb deprecation warnings are unchanged.
- `DRY_RUN=true ADMIN_TOKEN=t DATA_DIR=/tmp/tp-photo-auth-smoke .venv/bin/python scripts/test_auth_flow.py`:
  **ALL GREEN**.
- `node --check static/photo.js` and `node --check static/friends.js`: passed
  using the bundled Node runtime (parent checked friends.js too).
- `git diff --check`: clean. Parent static template review found no duplicate
  IDs or broken label/ARIA targets.
- A Flask test-client DRY_RUN created and queued a two-frame test strip:
  `/tmp/tp-photo-preview/photo-booth-preview.png` (576 × 1353) and
  `/tmp/tp-photo-preview/last_print.bin` (97,482 bytes), status `printed`.
  The preview was visually inspected: continuous framing, equal photo margins,
  ordered square frames, readable caption and unclipped timestamp. Source images
  are synthetic gradient fixtures, not a claim about real-photo print quality.

Coverage includes approved-session gating, invalid formats/animations/corrupt
files, byte/pixel/frame/caption/treatment/request caps, EXIF orientation and alpha,
58/80 mm output bounds, distinct treatments, preview-to-saved-pixel parity,
history ownership, user cap and queue/DB failure unwind, idempotent migration,
restart replay and byte-identical owner retries.

### Interface review results

Full review; vanilla JavaScript and existing plain CSS tokens. No actionable
interface-polish findings remain from source inspection.

| Category | Evidence inspected | Result |
| --- | --- | --- |
| Typography | Photo labels, hints, state/status/count copy and narrow-screen CSS | Clear; existing type tokens, tabular frame numbers and wrapping hints |
| Surfaces | Crop canvas, thumbnails, selected state, fields and preview paper | Clear; structural borders, white image outline on dark composer, 44 px controls |
| Animations | Preview invalidation, source-loading/send states, history scroll | Clear; no new staged motion; reduced-motion history scrolling |
| Icons | Existing mode index labels and text-only photo controls | Clear; no new icon system |
| Performance | Serial source decoding, 1600 px retained copies, 576 px uploads, preview revision/abort handling | Clear; old previews immediately invalidated and print waits for current output |

| Location | Candidate | Rejected because |
| --- | --- | --- |
| Photo crop editor | Drag-only panning | Explicit position sliders provide keyboard and touch control without trapping page scrolling |
| Photo history | Save original uploads for full editing | Durable final pixels support replay and retries without retaining originals or re-dithering saved photos |
| Composer | New entrance/thumbnail motion | Crop controls are frequent interactions; state borders and preview status already communicate changes |

Verdict: **Approve by source review**. Browser interaction, 10%-speed browser
motion inspection and physical printer output are **not verified**, per the
repository's no-browser-automation and DRY_RUN requirements.

### To test on the real setup

1. On phone and desktop, add 1–4 photos, select/crop/reorder/remove frames, switch
   treatments, type a caption and check that printing waits for the fresh preview.
2. Print one strip with faces using each treatment; inspect thermal contrast,
   margins, caption legibility and continuous raster output through the last frame.
3. Reopen a saved strip, toggle anonymous, and explicitly reprint. Confirm the
   saved pixels/caption and expected name header; verify a four-frame strip feeds
   and cuts cleanly on the physical printer.

### Deliberate limits

JPEG/PNG/WebP only; export HEIC as JPEG first. Browser originals are capped at
20 MB and 30 million pixels, while server frame uploads are capped at 4 MB each.
Frames use square crops and one treatment for the whole strip. Saved strips are
reprintable as finished pixels; new uploads are needed for fresh crop/caption
editing. The footer timestamps the actual enqueue time, so it can differ from a
preview left open across a minute boundary. Existing name-style-at-print-time and
at-least-once queue replay behavior remain unchanged.
