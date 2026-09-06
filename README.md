<p align="center">
  <img src="docs/banner.svg" alt="thermal-printer" width="100%" />
</p>

<p align="center">
  <em>A web GUI for my 80mm USB thermal receipt printer,<br/>
  plus a tiny public "send me a receipt" page friends can use to make things pop out of it.</em>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#deploy-to-a-raspberry-pi">Deploy</a> ·
  <a href="#configuration">Config</a> ·
  <a href="#tests">Tests</a> ·
  <a href="DEPLOY.md">Runbook</a>
</p>

---

## What's inside

**Main console** — `/admin` · at <https://print.cuzeth.com/admin>, unlocked with a 6-digit TOTP code from your authenticator app.

| Tab | What it does |
|---|---|
| **Compose** | Rich-text editor with live preview. Markup: `**bold**`, `*italic*`, `__underline__`, `~big~`, `#`/`##` headings, `>` centered, `-` bullets, `[ ]`/`[x]` todos, `---`/`===` rules, `!!!` cut markers. |
| **Image** | Drop an image, tune contrast/brightness/threshold, Floyd–Steinberg dither, print. |
| **Codes** | QR + 1D barcodes (CODE128, CODE39, EAN-13, EAN-8, UPC-A, ITF, CODABAR). Preview in the browser, print natively via ESC/POS. |
| **Widgets** | Morning briefing combo, weather (wttr.in), Hacker News, on-this-day (Wikipedia), advice, calendar, countdown, habit tracker, dice, ASCII art, "now" card. |
| **Arcade** | Sudoku, mazes and word searches, with exact receipt previews and solution QR codes. |
| **Labs** | Todo list, label maker, fake receipt. |
| **Hardware** | Cash drawer, beep, feed, cut, density, code page, LED (best-effort), status query. |
| **Console** | Raw ESC/POS byte sender with a built-in cheat sheet. Accepts hex (`1b 40 48 69`) or Python escapes (`\x1b@Hi\n`). |
| **Admin** | Approve / block / delete friends, review recent prints. |

**Paper Arcade:** pick a puzzle in the owner console's **arcade** tab and choose **new puzzle**. Check the receipt preview, then **print puzzle** to print that exact puzzle and its solution QR. The selection stays when you switch console tabs; **new puzzle** makes another. Printing and retrying never choose a different puzzle. The supported hardware widths are 576 px (80mm) and 384 px (58mm).

Everything is generated locally. Sudoku varies one verified, uniquely solvable 30-clue template by permuting digits, rows, columns and transposition; this preserves its single solution. Mazes have one path between every pair of cells. Word searches hide eight nature words in all eight directions. There are no external services, custom-word editors or scheduled arcade prints.

Scanning the QR opens `/arcade/solution/<signed identifier>` without a login. This is a deliberate **public, read-only solution page**: anyone with the receipt/link can see that generated puzzle's solution, and nothing from friend accounts or print history. Creation, preview and printing stay under `/api/admin/arcade/*` and require owner authentication. Signed payloads are bounded and validated before generation. The solution page offers a solved Sudoku grid, dotted maze path or underlined word letters with exact word coordinates.

Solution links use `https://print.cuzeth.com` by default. In explicit `DRY_RUN` or `DEV_BYPASS_ADMIN` mode they default to `http://127.0.0.1:5005` (or configured `PORT`), which opens only on that computer. To scan a local demo from a phone, set `PUBLIC_BASE_URL` to an existing reachable HTTP(S) origin that forwards to the app; keep the app bound to loopback. The override also supports another production hostname. Request Host and forwarding headers never select the printed address. If the configured origin or paper width changes after previewing, make a new puzzle before printing.

Keep the existing production `SECRET_KEY` persistent: it signs versioned puzzle identifiers as well as login sessions. Solution links survive app restarts with the same key and generator version; rotating the key invalidates old links. No puzzle database or expiry is needed. Future generator changes must retain version 1 behavior for existing receipts; fixture tests pin those identities. QR codes use four-module quiet zones and integer pixel modules, and all arcade rasters use the existing buffer-safe image printer helper.

**Friends page** — `/` · public at <https://print.cuzeth.com>, via Cloudflare Tunnel.

Friends register with a username + password, sit pending until you approve them from the Admin tab, then send messages, doodles or photo strips through the durable print queue. Text supports the same markup vocabulary as the composer. Prints include their name unless they choose anonymous.

**Photo booth:** choose 1–4 JPEG, PNG or WebP photos, select each frame to adjust its square crop with zoom and position sliders, and use **move earlier** to set the strip order. Pick soft grain, high contrast or solid ink, add an optional caption (160 characters), then check the server-rendered print preview and send. The print button waits for the current preview; changing a crop or treatment invalidates the previous one.

Photos are cropped on the device before upload. The editor accepts originals up to 20 MB and 30 million pixels, retains smaller editing copies, and sends only 576 px square PNGs. Export HEIC as JPEG first. The server independently limits uploads to four still JPEG/PNG/WebP frames, 4 MB and 30 million pixels each, within the existing 16 MB request cap. Only the finished monochrome strip is stored, with its caption; original photos and EXIF metadata are not retained. Tap a photo in **your prints** to preview and explicitly reprint those saved pixels. Add new photos to edit a new strip. Queue caps, restart replay, anonymous printing and owner retries work the same as other friend prints.

**Time capsules:** choose **time capsule** under delivery, pick a future date and time, then save your text, doodle or photo strip. Saved photo reprints can be scheduled too. The picker uses your browser's local timezone and shows the resolved instant; the server requires an explicit offset and stores UTC. Missing spring-forward times are rejected by the picker. For an ambiguous autumn time, the browser selects the earlier occurrence and shows its timezone abbreviation before you save. Sending successfully resets delivery to **print now**.

Up to 10 outstanding capsules per friend and 200 across the printer can wait, each up to 365 days ahead. These SQLite limits include claimed capsules and bound the waiting image backlog separately from the existing queue caps. Waiting capsules stay at the top of **your prints**, even after newer receipts; they show the effective delivery date and a **cancel capsule** button. Cancellation is atomic and ends when the queue claims a capsule. Blocking a sender cancels their pending capsules; deleting them removes their rows. Already-started physical prints cannot be recalled.

The dispatcher checks every 15 seconds and catches up overdue capsules after a restart. Due jobs honor the same 3-per-friend and 50-job queue limits; a full queue leaves them waiting for another tick. Printer failures become **didn't print** in history, with the owner's existing retry action; they are not retried indefinitely. The existing crash tradeoff still applies: a restart during the physical print may print that receipt twice. The receipt footer preserves when the capsule was created; delivery dates appear in the web history.

**Optional quiet hours:** set both `FRIEND_QUIET_START=22:00` and `FRIEND_QUIET_END=07:00` to hold friend prints overnight. Both empty (the default) means off. `FRIEND_QUIET_TIMEZONE` defaults to `America/Phoenix` and accepts IANA timezone names independently of the Pi's local timezone. Restart after changing these settings. The start is inclusive and end exclusive; same-day windows work too. In DST zones, nonexistent end times release at the next allowed real minute, and repeated minutes follow the configured wall-clock rule. Quiet hours apply to immediate sends, explicit capsules, and jobs reaching the worker after the window starts, including restart recovery. The effective release time appears after saving and in history. Already accepted jobs that cross the boundary are retained even if that temporarily exceeds the waiting cap. Owner manual prints and scheduled briefings keep their normal behavior.

**Security model:** one Cloudflare Tunnel, one hostname, and the app defends itself. `print.cuzeth.com` fronts `127.0.0.1:5005` with no edge auth ([deploy/cloudflared-config.yml](deploy/cloudflared-config.yml)); friends get signed-cookie sessions, and the owner unlocks `/admin` with an RFC 6238 TOTP code (`auth/totp.py`, secret in `.env`, enrolled in any authenticator app). Admin sessions expire after 12 hours; each code is single-use (replay guard), and the login carries per-IP *and* global failure lockouts because 6-digit codes are a small space (`auth/admin.py`). `/api/admin/*` also accepts `Bearer ADMIN_TOKEN` for curl and the smoke tests. Flask must bind to `127.0.0.1` so the tunnel is the only way in — the rate limiters trust the last `X-Forwarded-For` hop, which only Cloudflare should be appending.

Abuse limits (all in-memory, reset on restart):
- friend prints go through a FIFO queue (50 jobs max, 3 in-flight per user)
- 10 failed logins / 15 min per username
- 30 failed logins / 15 min per IP
- 5 signups / hour per IP
- TOTP login: 10 failures / 15 min per IP, 30 globally

---

## Quick start

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DEV_BYPASS_ADMIN=true COOKIE_SECURE=false python3 app.py
```

Open <http://localhost:5005/admin> for the console, <http://localhost:5005> for the friends page.

Both env vars are load-bearing in local dev:
- `DEV_BYPASS_ADMIN=true` — skips the TOTP login so `/admin` and `/api/admin/*` open without a code.
- `COOKIE_SECURE=false` — the default is `true` (prod is HTTPS), and without it over plain HTTP the browser silently drops the session cookie and every friend request fails as "not signed in."

For a local friends demo, open <http://localhost:5005>, click **Create an account**, pick any username + password (8+ chars), then approve yourself from the Admin tab in the console.

Set `DRY_RUN=true` to skip USB and dump ESC/POS bytes to `./data/last_print.bin` instead. Useful for iterating on layouts without burning paper.

Set `FLASK_DEBUG=1` **locally only** if you want the Werkzeug reloader. Never on the Pi — the app is public at print.cuzeth.com and the debugger page is RCE.

---

## Deploy to a Raspberry Pi

Short version:

1. Flash Raspberry Pi OS Lite (64-bit), set hostname `thermal-printer`, enable SSH.
2. `bash deploy/setup.sh` — installs system deps, creates a venv, pip-installs, drops the udev rule and systemd unit.
3. `cp .env.example .env` and fill in `SECRET_KEY`, `ADMIN_TOKEN`, and `TOTP_SECRET`. Commands to generate them:
   ```sh
   python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
   python3 -c "import secrets; print('ADMIN_TOKEN=' + secrets.token_urlsafe(32))"
   python3 scripts/gen_totp.py    # prints a QR to scan + the TOTP_SECRET line
   ```
4. Set up the Cloudflare Tunnel — `cloudflared` install, tunnel creation, and the DNS route for print.cuzeth.com are step-by-step in [DEPLOY.md](DEPLOY.md). No Access application, no Transform Rules — auth lives in the app.
5. `sudo systemctl start thermal-printer && journalctl -u thermal-printer -f`.

The systemd unit runs gunicorn with `--workers 1 --threads 4` — single-worker is load-bearing because the USB lock and the friend print queue both live in process-local memory.

Full step-by-step (udev, tunnel wiring, troubleshooting) lives in [DEPLOY.md](DEPLOY.md).

---

## Configuration

Everything is env-driven with sensible defaults. Copy [`.env.example`](.env.example) to `.env` on the Pi and fill in what you need.

| Variable | Required? | Default | Notes |
|---|---|---|---|
| `SECRET_KEY` | yes (prod) | random per boot | Flask sessions and Paper Arcade links. Persist across restarts to keep both valid. |
| `ADMIN_TOKEN` | yes (prod) | random per boot | Bearer alternative to the TOTP session for `/api/admin/*` — used by curl and the smoke tests. |
| `TOTP_SECRET` | yes (prod) | (empty) | Base32 secret behind the `/admin` login. Generate + enroll with `python3 scripts/gen_totp.py`. Empty = TOTP login fails closed (Bearer still works). |
| `HOST` / `PORT` | no | `127.0.0.1` / `5005` | Must be `127.0.0.1` in prod — only cloudflared may reach the app, because the rate limiters trust the last `X-Forwarded-For` hop. |
| `DEV_BYPASS_ADMIN` | no | `false` | Skips the TOTP login entirely. Set `true` for local dev; **never** on the Pi. |
| `COOKIE_SECURE` | no | `true` | Secure-flag session cookies. Default fits prod (both hostnames are HTTPS). For local HTTP dev, set `COOKIE_SECURE=false` — otherwise the browser drops the session cookie and friends can't stay signed in. |
| `DRY_RUN` | no | `false` | Write ESC/POS bytes to `data/last_print.bin` instead of USB. |
| `PUBLIC_BASE_URL` | no | `https://print.cuzeth.com` | Origin for Arcade solution QR links; explicit DRY_RUN/dev bypass defaults to loopback and `PORT`. Set a reachable origin for phone scans of a local demo. |
| `DEFAULT_LOCATION` | no | `Phoenix` | Fallback city for the weather widget and morning briefing; also prefills the GUI inputs. |
| `BRIEFING_SCHEDULE` | no | (off) | Set `HH:MM` (24h) to auto-print the morning briefing daily. Empty = off. |
| `FRIEND_QUIET_START` / `FRIEND_QUIET_END` | no | (off) | Set both to different `HH:MM` times to hold friend prints during that window; leave both empty to disable. |
| `FRIEND_QUIET_TIMEZONE` | no | `America/Phoenix` | IANA timezone for friend quiet hours, independent of browser and Pi timezones. |
| `USB_VENDOR_ID` / `USB_PRODUCT_ID` | no | `0x0483` / `0x5720` | Match your printer. |
| `RECEIPT_WIDTH` / `PRINTER_PIXEL_WIDTH` | no | `42` / `576` | 42 cols / 576 px = 80mm. For 58mm use `32` / `384`. |
| `DATA_DIR` | no | `./data` | SQLite + `last_print.bin` live here. |
| `FLASK_DEBUG` | — | unset | **Never** set on the Pi. |

---

## Tests

```sh
pip install pytest
pytest                              # render / widgets / image / routes
python scripts/test_auth_flow.py    # end-to-end auth against the Flask test client
```

CI runs both on push + PR (see [.github/workflows/ci.yml](.github/workflows/ci.yml)). No USB is touched — everything exercises DRY_RUN.

---

## Stack

- **Backend** · Python 3.12 · Flask · gunicorn · python-escpos · Pillow · SQLite (WAL)
- **Frontend** · vanilla JS, no build step
- **Auth** · werkzeug scrypt for passwords, signed-cookie sessions for friends, stdlib RFC 6238 TOTP for the owner (bearer token for scripts)
- **Hosting** · Raspberry Pi · one Cloudflare Tunnel, one hostname: print.cuzeth.com (friends at `/`, console at `/admin`)

---

## Project layout

<details>
<summary>click to expand</summary>

```
app.py                  Flask entrypoint + route definitions
config.py               env-driven config
printer.py              python-escpos context manager + USB lock
smoke.py                quick DRY_RUN sanity exerciser
features/
  codes.py              QR + barcode (preview in PIL, print via ESC/POS native)
  hardware.py           cash drawer, cut, feed, density, status, raw bytes
  image.py              upload → grayscale → dither/threshold → 1-bit
  photo.py              bounded photo frames → thermal strip + caption
  delivery.py           UTC capsule validation + quiet-hours calendar math
  arcade.py             versioned local puzzles, signed solution links + raster receipts
  led.py                LED protocol candidates for vendor-specific units
  markup.py             shared inline-markup grammar (spans: bold/underline/big)
  render.py             PIL-based rich-text rasterizer (growable canvas)
  text.py               plain-text composer (ROM-font path)
  widgets.py            weather / HN / on-this-day / dice / todo / …
auth/
  totp.py               stdlib RFC 6238 TOTP (the /admin login)
  admin.py              /api/admin/auth/{login,logout} + lockouts + replay guard
  ratelimit.py          shared in-memory sliding-window buckets
  db.py                 SQLite schema + CRUD (users, messages)
  session.py            require_allowed / require_admin + session helpers
  blueprint.py          /api/auth/{register,login,logout} + /me
templates/
  index.html            main GUI (tabs), served at /admin
  admin_login.html      the TOTP code prompt
  friends.html          public friends page, served at /
  arcade_solution.html  public, signed-link puzzle solutions only
static/
  app.js / style.css    main GUI
  friends.js / .css     friends page
  photo.js             local photo crops + server preview + saved-strip reprints
tests/                  pytest: render / widgets / image / routes / security / totp / queue
scripts/
  test_auth_flow.py     Flask-test-client auth smoke
  gen_totp.py           mint TOTP_SECRET + QR for authenticator enrollment
deploy/
  thermal-printer.service   systemd unit (gunicorn, workers=1)
  99-thermal-printer.rules  udev rule (USB access for non-root)
  cloudflared-config.yml    Cloudflare Tunnel ingress (one hostname)
  setup.sh                  idempotent Pi bootstrapper
docs/banner.svg         the receipt up top
.github/workflows/ci.yml    pytest + auth smoke on push/PR
DEPLOY.md               Raspberry Pi run-book
MIGRATION.md            one-time cutover: Tailscale Funnel -> Cloudflare + TOTP
.env.example            what to fill into .env on the Pi
```

</details>
