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

**Main console** — `/` · private, meant to live on your tailnet.

| Tab | What it does |
|---|---|
| **Compose** | Rich-text editor with live preview. Markup: `**bold**`, `__underline__`, `~big~`, `#`/`##` headings, `>` centered, `-` bullets, `[ ]`/`[x]` todos, `---`/`===` rules, `!!!` cut markers. |
| **Image** | Drop an image, tune contrast/brightness/threshold, Floyd–Steinberg dither, print. |
| **Codes** | QR + 1D barcodes (CODE128, CODE39, EAN-13, EAN-8, UPC-A, ITF, CODABAR). Preview in the browser, print natively via ESC/POS. |
| **Widgets** | Morning briefing combo, weather (wttr.in), Hacker News, on-this-day (Wikipedia), advice, calendar, countdown, habit tracker, dice, ASCII art, "now" card. |
| **Labs** | Todo list, label maker, fake receipt. |
| **Hardware** | Cash drawer, beep, feed, cut, density, code page, LED (best-effort), status query. |
| **Console** | Raw ESC/POS byte sender with a built-in cheat sheet. Accepts hex (`1b 40 48 69`) or Python escapes (`\x1b@Hi\n`). |
| **Admin** | Approve / block / delete friends, review recent prints. |

**Friends page** — `/m/` · public at <https://print.cuzeth.com>, via Cloudflare Tunnel.

Friends register with a username + password, sit pending until you approve them from the Admin tab, then send you messages that print immediately with their name attached. Supports the same markup vocabulary as the composer.

**Security model:** two local proxies front `127.0.0.1:5005`. `tailscale serve` publishes the app to the tailnet only and injects the `Tailscale-User-Login` header that private routes (`/`, `/api/*`) require. `cloudflared` publishes the friends page to the internet at print.cuzeth.com — and since it forwards client headers verbatim, three walls keep forged identity headers out: the tunnel's ingress allowlists only `/m`, `/api/m/*`, `/static/*`, and `/api/ping` ([deploy/cloudflared-config.yml](deploy/cloudflared-config.yml)); a Cloudflare Transform Rule strips `Tailscale-User-Login` at the edge; and the app refuses tailnet status to any request bearing Cloudflare's `CF-Ray` marker (`auth/tailnet.py`). Flask must bind to `127.0.0.1` so nothing bypasses the proxies.

Abuse limits (all in-memory, reset on restart):
- friend prints go through a FIFO queue (50 jobs max, 3 in-flight per user)
- 10 failed logins / 15 min per username
- 30 failed logins / 15 min per IP
- 5 signups / hour per IP

---

## Quick start

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DEV_BYPASS_TAILNET=true COOKIE_SECURE=false python3 app.py
```

Open <http://localhost:5005>. The startup banner prints a dev `ADMIN_TOKEN` — the main GUI inlines it automatically, so you don't need to copy it.

Both env vars are load-bearing in local dev:
- `DEV_BYPASS_TAILNET=true` — skips the `Tailscale-User-Login` header check so private routes (`/`, `/api/*`) don't return 403.
- `COOKIE_SECURE=false` — the default is `true` (both prod doors are HTTPS), and without it over plain HTTP the browser silently drops the session cookie and every `/m/*` request fails as "not signed in."

For a local friends demo, open <http://localhost:5005/m/>, click **Create an account**, pick any username + password (8+ chars), then approve yourself from the Admin tab in the main GUI.

Set `DRY_RUN=true` to skip USB and dump ESC/POS bytes to `./data/last_print.bin` instead. Useful for iterating on layouts without burning paper.

Set `FLASK_DEBUG=1` **locally only** if you want the Werkzeug reloader. Never on the Pi — `/m/*` is public at print.cuzeth.com and the debugger page is RCE.

---

## Deploy to a Raspberry Pi

Short version:

1. Flash Raspberry Pi OS Lite (64-bit), set hostname `thermal-printer`, enable SSH.
2. `bash deploy/setup.sh` — installs system deps, creates a venv, pip-installs, drops the udev rule and systemd unit.
3. `cp .env.example .env` and fill in `SECRET_KEY` + `ADMIN_TOKEN`. Commands to generate them:
   ```sh
   python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
   python3 -c "import secrets; print('ADMIN_TOKEN=' + secrets.token_urlsafe(32))"
   ```
4. Install Tailscale: `curl -fsSL https://tailscale.com/install.sh | sh`. Run `sudo tailscale serve --bg 5005` to publish the console to your tailnet (and only your tailnet) — private routes are gated at the Flask layer via the `Tailscale-User-Login` header.
5. Set up the Cloudflare Tunnel for print.cuzeth.com — `cloudflared` install, tunnel creation, DNS route, and the header-strip Transform Rule are step-by-step in [DEPLOY.md](DEPLOY.md).
6. `sudo systemctl start thermal-printer && journalctl -u thermal-printer -f`.

The systemd unit runs gunicorn with `--workers 1 --threads 4` — single-worker is load-bearing because the USB lock and the `/api/m/print` queue both live in process-local memory.

Full step-by-step (udev, Tailscale wiring, troubleshooting) lives in [DEPLOY.md](DEPLOY.md).

---

## Configuration

Everything is env-driven with sensible defaults. Copy [`.env.example`](.env.example) to `.env` on the Pi and fill in what you need.

| Variable | Required? | Default | Notes |
|---|---|---|---|
| `SECRET_KEY` | yes (prod) | random per boot | Flask session signing. Persist to keep sessions across restarts. |
| `ADMIN_TOKEN` | yes (prod) | random per boot | Bearer gate for `/api/admin/*` and every private console route. |
| `HOST` / `PORT` | no | `127.0.0.1` / `5005` | Must be `127.0.0.1` in prod — only the two local proxies (tailscaled + cloudflared) may reach the app, because the tailnet gate trusts Tailscale's identity headers. |
| `DEV_BYPASS_TAILNET` | no | `false` | Skips the `Tailscale-User-Login` header check. Set `true` for local dev; **never** on the Pi. |
| `COOKIE_SECURE` | no | `true` | Secure-flag session cookies. Default fits prod (print.cuzeth.com and the tailnet console are both HTTPS). For local HTTP dev, set `COOKIE_SECURE=false` — otherwise the browser drops the session cookie and friends can't stay signed in. |
| `DRY_RUN` | no | `false` | Write ESC/POS bytes to `data/last_print.bin` instead of USB. |
| `DEFAULT_LOCATION` | no | `Phoenix` | Fallback city for the weather widget and morning briefing; also prefills the GUI inputs. |
| `BRIEFING_SCHEDULE` | no | (off) | Set `HH:MM` (24h) to auto-print the morning briefing daily. Empty = off. |
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
- **Auth** · werkzeug scrypt for passwords, signed-cookie sessions for friends, bearer token for owner
- **Hosting** · Raspberry Pi · Cloudflare Tunnel for the public friends page (print.cuzeth.com) · `tailscale serve` for the private console (tailnet gating via identity headers)

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
  led.py                LED protocol candidates for vendor-specific units
  markup.py             shared inline-markup grammar (spans: bold/underline/big)
  render.py             PIL-based rich-text rasterizer (growable canvas)
  text.py               plain-text composer (ROM-font path)
  widgets.py            weather / HN / on-this-day / dice / todo / …
auth/
  tailnet.py            require_tailnet decorator (Tailscale identity header gate)
  db.py                 SQLite schema + CRUD (users, messages)
  session.py            require_allowed / require_admin / require_owner
  blueprint.py          /api/m/auth/{register,login,logout} + /me
templates/
  index.html            main GUI (tabs)
  friends.html          public friends page
static/
  app.js / style.css    main GUI
  friends.js / .css     friends page
tests/                  pytest: render / widgets / image / routes / security / queue
scripts/
  test_auth_flow.py     Flask-test-client auth smoke
deploy/
  thermal-printer.service   systemd unit (gunicorn, workers=1)
  99-thermal-printer.rules  udev rule (USB access for non-root)
  cloudflared-config.yml    Cloudflare Tunnel ingress (public path allowlist)
  setup.sh                  idempotent Pi bootstrapper
docs/banner.svg         the receipt up top
.github/workflows/ci.yml    pytest + auth smoke on push/PR
DEPLOY.md               Raspberry Pi run-book
.env.example            what to fill into .env on the Pi
```

</details>
