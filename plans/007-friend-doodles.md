# Plan 007: Friend doodles — draw on /m/, print through the same queue

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat eb34102..HEAD -- app.py features/widgets.py static/friends.js templates/friends.html static/friends.css tests/test_friend_queue.py`
> `eb34102` is the plan-001 commit this plan's excerpts are based on. If
> `friend_print` / `_print_worker` don't match the excerpts, STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (touches the queue worker and `friend_print`)
- **Depends on**: 001 (merged or stacked — excerpts assume its shape); 002 recommended first (input guards)
- **Category**: direction (owner-approved 2026-07-06: "i would LOVE this")
- **Planned at**: `eb34102` (`advisor/001` tip), 2026-07-06

## Why this matters

The owner console has a full Image tab; friends can only send text. The
owner explicitly wants friends to draw things that pop out of the printer.
Friends are **trusted, approved people** — per the owner (2026-07-06), do
NOT add content-moderation or abuse ceremony beyond the caps that already
exist (per-user in-flight cap of 3, 50-job queue, 16MB body cap from plan
002). A doodle is a small canvas on `/m/` that prints as a 1-bit image
between the same "from <name>" header and timestamp footer text messages
get.

Owner constraint: no emoji anywhere in UI copy.

## Current state

(All excerpts as of `eb34102` — plan 001's shape.)

- `app.py` friend-print region:
  - `_MAX_MSG_LEN = 800` at line ~753; queue tuple is
    `(user_id, message_id, formatted_body)` per the comment at ~765.
  - `_print_worker()` (~785–812), the relevant core:

    ```python
    def _print_worker() -> None:
        while True:
            user_id, msg_id, formatted = _PRINT_QUEUE.get()
            status = "printed"
            try:
                _print_body(formatted)
            except Exception as e:
                ...
    ```
  - `friend_print()` (~848–911) after plan 001: cap check under
    `_inflight_lock` → `msg_id = None` → `try:` `log_message` +
    `qsize()` + `put_nowait((user["id"], msg_id, formatted))` →
    `except queue.Full:` (503, dec + delete row) → `except Exception:`
    (500 `"kind": "server"`, dec + best-effort delete + traceback) →
    success return `{"ok": True, "queued": True, "ahead": ahead}`.
  - Print helpers: `_print_image` (imported from `printer` at line 31),
    `_send_rich` / `_print_body` / `_print_sections` (lines 93–133),
    `footer(p)` cuts.
- `features/widgets.py:821–849` — `friend_message(username, body, *,
  style="plain", anonymous=False)` builds:
  header (`_name_header(username, style)` or `"## from anonymous"`),
  `"==="`, blank, wrapped body paragraphs (with the `!!!` cut directive
  stripped for friends), then `"", "---", f"> {timestamp}", "---"`.
  The timestamp format is
  `dt.datetime.now().strftime("%a %b %-d · %I:%M %p").lower()`.
- `features/image.py` — `process(image_bytes, ProcessOptions)` (decode →
  resize to width → grayscale → threshold/dither → 1-bit; plan 002 added
  non-image → `ValueError` and `MAX_INPUT_PIXELS`), and
  `pad_to_printer_width(img)`.
- `features/render.py` — `render_markup(markup) -> PIL.Image` (import in
  `app.py` as `render_feat`).
- `static/friends.js` — `postJSON`/`getJSON` helpers (21–36), `applyMe`
  state machine (72–97), `sendMessage()` (269–288), form wiring 336–354.
- `templates/friends.html` — allowed card 146–197: `#msg-form` textarea +
  `#msg-anon` toggle + `#preview-paper` + submit row, then `#history`.
- `tests/test_friend_queue.py` — `_signed_in_client` helper + queue tests
  incl. plan 001's three; the patterns to copy.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Full suite | `.venv/bin/python -m pytest -q` | all pass; +5 vs. pre-plan baseline |
| Queue tests | `.venv/bin/python -m pytest tests/test_friend_queue.py -q` | all pass |
| Auth smoke | `DRY_RUN=true ADMIN_TOKEN=t DATA_DIR=/tmp/tp-smoke-007 .venv/bin/python scripts/test_auth_flow.py` | exits 0 |

## Scope

**In scope**:
- `app.py` — `_print_worker` job dispatch, an `_enqueue_friend_print`
  helper extracted from `friend_print`, new route
  `/api/m/print/doodle`, a `_print_doodle` helper
- `features/widgets.py` — extract `friend_frame(username, style,
  anonymous) -> tuple[str, str]` and reuse it in `friend_message`
- `static/friends.js`, `templates/friends.html`, `static/friends.css` —
  draw panel
- `tests/test_friend_queue.py` — new tests

**Out of scope** (do NOT touch):
- `features/render.py`, `features/image.py` internals — consume them as-is.
- The owner console (Image tab) — unrelated code path.
- `/api/m/preview` — the canvas IS the doodle preview; no server preview.
- Response shapes of existing routes; `auth/*`; rate limits.

## Git workflow

- Branch: `advisor/007-friend-doodles` (stack on the latest advisor branch
  if unmerged, else main)
- Commit style: `Friends page: doodle canvas that prints through the queue`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: `friend_frame` in `features/widgets.py`

Extract the header/footer construction from `friend_message` into:

```python
def friend_frame(username: str, *, style: str = "plain",
                 anonymous: bool = False) -> tuple[str, str]:
    """Markup for the lines that frame a friend print — the "from <name>"
    header and the timestamp footer. Shared by text messages and doodles
    so the two kinds are visually siblings on paper."""
```

returning `(header_markup, footer_markup)` where header is
`"{header line}\n==="` and footer is `"---\n> {timestamp}\n---"` with the
exact same `_name_header`/anonymous logic and timestamp format as today.
Rewrite `friend_message` to call it (assemble: header, blank line, body
paragraphs as today, blank line, footer). Output of `friend_message` must
be byte-identical to before.

**Verify**: `.venv/bin/python -m pytest tests/test_friend_queue.py tests/test_widgets.py -q` → all pass (these pin `friend_message` behavior).

### Step 2: Generalize the queue job + worker in `app.py`

- Change the queue comment/type to `(user_id, message_id, job_dict)`;
  jobs: `{"kind": "text", "body": <markup str>}` or
  `{"kind": "doodle", "image": <1-bit PIL image>, "header": <markup>,
  "footer": <markup>}`.
- In `friend_print`, build `job = {"kind": "text", "body": formatted}`.
- Extract the entire post-001 bookkeeping block (cap check → try →
  `queue.Full` → generic except → success return) into:

  ```python
  def _enqueue_friend_print(user: dict, history_body: str, job: dict):
      """Shared bookkeeping for every friend print kind: per-user cap,
      optimistic history row, queue insert, and the crash-safe unwind
      from plan 001. Returns a Flask response."""
  ```

  `friend_print` becomes: validate body → build `formatted` via
  `widgets.friend_message` → `return _enqueue_friend_print(user, body,
  {"kind": "text", "body": formatted})`. Response shapes and status codes
  must be unchanged (the existing tests pin them).
- Worker dispatch:

  ```python
  user_id, msg_id, job = _PRINT_QUEUE.get()
  status = "printed"
  try:
      if job["kind"] == "doodle":
          _print_doodle(job)
      else:
          _print_body(job["body"])
  ```

- `_print_doodle(job)` next to `_print_body`:

  ```python
  def _print_doodle(job: dict) -> None:
      """Header, the drawing, timestamp footer — one tear-off. Header and
      footer are rasterized like any markup; the doodle goes between them
      as its own transfer, same buffer-safe shape as _print_sections."""
      header = render_feat.render_markup(job["header"])
      footer_img = render_feat.render_markup(job["footer"])
      with open_printer() as p:
          _print_image(p, header)
          _print_image(p, job["image"])
          _print_image(p, footer_img)
          footer(p)
  ```

**Verify**: `.venv/bin/python -m pytest tests/test_friend_queue.py -q` →
all existing tests pass unchanged (proves the refactor preserved shapes).

### Step 3: The doodle endpoint in `app.py`

Constants near `_MAX_MSG_LEN`:

```python
# Canvas PNGs are tiny (white bg + strokes compress well); 2MB of decoded
# PNG is already far beyond any honest doodle. Guards the b64 decode, the
# 16MB global body cap guards the transport.
_MAX_DOODLE_BYTES = 2 * 1024 * 1024
_DOODLE_PREFIX = "data:image/png;base64,"
```

Route, mirroring `friend_print`'s structure and docstring voice:

```python
@app.post("/api/m/print/doodle")
@require_allowed
def friend_print_doodle():
    user = current_user()
    data = request.get_json(silent=True) or {}
    raw = data.get("image") or ""
    if not isinstance(raw, str) or not raw.startswith(_DOODLE_PREFIX):
        return jsonify({"ok": False, "error": "no drawing attached",
                        "kind": "input"}), 400
    try:
        png = base64.b64decode(raw[len(_DOODLE_PREFIX):], validate=True)
    except binascii.Error:
        return jsonify({"ok": False, "error": "bad image data",
                        "kind": "input"}), 400
    if len(png) > _MAX_DOODLE_BYTES:
        return jsonify({"ok": False, "error": "drawing too large",
                        "kind": "input"}), 400
    try:
        img = image_feat.process(
            png, image_feat.ProcessOptions(mode="threshold", threshold=160))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e), "kind": "input"}), 400
    # An untouched canvas thresholds to pure white — nothing to print.
    if img.convert("L").getextrema()[0] == 255:
        return jsonify({"ok": False, "error": "draw something first",
                        "kind": "input"}), 400
    img = image_feat.pad_to_printer_width(img)
    header, footer_markup = widgets.friend_frame(
        user["username"],
        style=user.get("name_style") or "plain",
        anonymous=bool(data.get("anonymous", False)),
    )
    return _enqueue_friend_print(user, "(doodle)", {
        "kind": "doodle", "image": img,
        "header": header, "footer": footer_markup,
    })
```

Add `import base64` / `import binascii` at the top of `app.py` in house
style. History body is the literal string `"(doodle)"`.

**Verify**: `.venv/bin/python -m pytest -q` → all pass (new tests come next).

### Step 4: Frontend — draw panel on `/m/`

`templates/friends.html`, allowed card: a two-tab mode switch above the
form (buttons `#mode-write` / `#mode-draw`, plain text labels "write" /
"draw" — no emoji), `#msg-form` becomes the write panel, and a new
`#doodle-panel` (hidden by default):

```html
<div id="doodle-panel" hidden>
  <canvas id="doodle-canvas" width="576" height="576"></canvas>
  <p class="hint">draw with your finger or mouse. it prints exactly this, in black and white.</p>
  <label class="anon-toggle">
    <input type="checkbox" id="doodle-anon" />
    <span>send anonymously <em>(hides your name on this one)</em></span>
  </label>
  <div class="row">
    <button type="button" class="primary" id="doodle-send">Print it</button>
    <button type="button" class="ghost" id="doodle-clear">Clear</button>
  </div>
</div>
```

`static/friends.js`:
- Mode switch: toggling hides one panel, shows the other, `.active` class
  on the tab buttons.
- Canvas: init context once — white fill, `strokeStyle="#000"`,
  `lineWidth=8`, `lineCap="round"`, `lineJoin="round"`. Pointer events
  (`pointerdown/pointermove/pointerup/pointerleave`) with
  `setPointerCapture`; map client coords → canvas coords via
  `getBoundingClientRect` scaling (CSS size ≠ backing 576×576).
- `#doodle-clear` refills white.
- `#doodle-send`: disable button (match the `#msg-form` submit pattern at
  lines 336–347), `postJSON("/api/m/print/doodle", { image:
  canvas.toDataURL("image/png"), anonymous: $("#doodle-anon").checked })`,
  then the same success choreography as `sendMessage()` (flash, honest
  queued toast using `j.ahead`, `loadHistory()`), clear the canvas, reset
  the anon box.
- `historyItem`: when `msg.body === "(doodle)"`, render the body as
  `(doodle)` with a `history-doodle` class and skip the click-to-restore
  handler (restoring a placeholder into the textarea is nonsense).

`static/friends.css`: canvas full card width, square aspect
(`aspect-ratio: 1`), `touch-action: none`, `cursor: crosshair`, subtle
border matching existing card borders; tab-button styles echoing
`.style-chip`.

**Verify**: `rg -n "doodle" templates/friends.html static/friends.js static/friends.css` → wired in all three;
grep the new UI strings for emoji: `rg -n "[\x{1F300}-\x{1FAFF}]" templates/friends.html static/friends.js` → no matches.

### Step 5: Tests — `tests/test_friend_queue.py`

Helper at top of the new tests (PIL is already a test dep via
`tests/test_image.py`):

```python
def _doodle_data_url(blank: bool = False) -> str:
    img = Image.new("RGB", (576, 576), (255, 255, 255))
    if not blank:
        ImageDraw.Draw(img).rectangle([100, 100, 300, 300], fill=(0, 0, 0))
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
```

1. `test_doodle_prints_and_lands_in_history(client)` — signed-in allowed
   user → POST `/api/m/print/doodle` with a real doodle → 200,
   `queued is True`; `_PRINT_QUEUE.join()`; history row with body
   `"(doodle)"` and `status == "printed"` (DRY_RUN worker really ran the
   doodle job).
2. `test_doodle_rejects_blank_canvas(client)` — blank data URL → 400,
   error mentions "draw".
3. `test_doodle_rejects_garbage_payloads(client)` — each of: missing
   `image`, `"image": "hello"`, `"image": "data:image/png;base64,@@@"` →
   400 with `kind == "input"`.
4. `test_doodle_requires_approval(client)` — pending user (no
   `set_status`) → 403.
5. `test_doodle_counts_against_user_cap(client)` — set
   `_inflight[user] = _PER_USER_QUEUE_CAP` under the lock (copy
   `test_friend_print_per_user_cap`) → doodle POST → 429
   `kind == "user_cap"`; restore in `finally`.

**Verify**: `.venv/bin/python -m pytest tests/test_friend_queue.py -q` →
all pass (+5); full suite → +5 vs. pre-plan baseline; auth smoke exits 0.

## Test plan

Covered in Step 5. The pipeline is exercised end-to-end in DRY_RUN by
test 1. Manual checklist for the owner (include in your report):

- **to test:** on a phone, `/m/` → draw tab → scribble → Print it →
  confirm it prints with your name header + timestamp footer; check
  "(doodle)" rows in history and on the Admin tab; try anonymous mode.

## Done criteria

- [ ] Full suite exits 0, +5 tests vs. pre-plan baseline
- [ ] `rg -n "api/m/print/doodle" app.py static/friends.js` → both hit
- [ ] `rg -n "def friend_frame" features/widgets.py` → one match, and
      `friend_message` calls it
- [ ] Existing `tests/test_friend_queue.py` tests unmodified and passing
- [ ] No emoji in any new UI string
- [ ] `git status --porcelain` limited to in-scope files
- [ ] `plans/README.md` status row updated (unless reviewer maintains it)

## STOP conditions

Stop and report back (do not improvise) if:

- `friend_print` doesn't match plan 001's shape (this plan's refactor
  assumes it) or `_print_worker` has diverged from the excerpt.
- Making `friend_message` byte-identical through `friend_frame` proves
  impossible (e.g. the timestamp is computed twice and disagrees across a
  minute boundary — compute it once inside `friend_frame`).
- Any existing queue test fails after Step 2 — the refactor must be
  invisible to them.
- You need to persist doodle images in the DB or filesystem to make
  history "nicer" — explicitly deferred; history shows `"(doodle)"` only.

## Maintenance notes

- The queue job dict is the extension point for future kinds; keep
  `_enqueue_friend_print` the single bookkeeping path.
- Doodles are not persisted — history/admin show `"(doodle)"`. If the
  owner ever wants doodle thumbnails in history, that's a schema change
  (store the PNG) — deliberately deferred.
- Reviewer: scrutinize the worker dispatch (a malformed job dict must not
  wedge the worker — the existing per-job try/except already covers it)
  and that `friend_message` output is truly unchanged.
