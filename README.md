# thermal-printer

A web GUI for my 80mm USB thermal receipt printer, plus a tiny public
"send me a message" page friends can use to make things pop out of it.

Not a product. Just fun stuff to do with a roll of thermal paper.

## What's inside

**Main GUI (`/`)** — private, meant to live on my tailnet. Tabs for:
- **Compose** — rich-text composer with live preview (`**bold**`, `__italic__`, `~strike~`, `# heading`, `## subheading`, `===`, `---`, `> centered`, `!!!` cut marker)
- **Image** — upload, threshold/dither, preview, print
- **Codes** — QR + 1D barcodes with preview
- **Widgets** — quote, dad joke, haiku, magic-8-ball, weather, dice, todo, receipt, label, ASCII art, "now" card
- **Labs** — experimental renders
- **Hardware** — cash drawer kick, beep, feed, cut, density, code page, LED (for printers that have one), status query
- **Console** — raw ESC/POS byte sender + cheat sheet
- **Admin** — approve/block friends, see recent prints

**Friends page (`/m/`)** — public-facing. Friends register with a
username + password, I approve them, they send me messages that print
immediately with their name attached. Rate-limited to one message per
10 seconds. Failed logins throttled to 10 per 15 minutes per username.

## Run it locally

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Open <http://localhost:5005>. The startup banner prints a dev
`ADMIN_TOKEN` — the main GUI inlines it automatically, so you don't
need to copy it.

For a local "friends" demo, open <http://localhost:5005/m/>, click
**Create an account**, pick any username + password (8+ chars), then
approve yourself from the Admin tab in the main GUI.

Set `DRY_RUN=true` in the env to skip USB and dump ESC/POS bytes to
`./data/last_print.bin` instead. Useful for iterating on layouts
without burning paper.

## Deploy to the NAS

The short version:

1. Build an `amd64` image on your Mac.
2. Ship it to the DS225+ (save → scp → load, OR build directly on the NAS).
3. `docker compose up -d` on the NAS.
4. Install the Tailscale Synology package. Use `tailscale serve` for
   the main GUI (tailnet-only) and `tailscale funnel --set-path=/m`
   for the friends page (public via a `*.ts.net` URL).

Full step-by-step, including the DSM USB gotchas and environment
variables, lives in [DEPLOY.md](DEPLOY.md).

## Project layout

```
app.py                  Flask entrypoint
config.py               env-driven config
printer.py              python-escpos context manager + USB lock
features/
  codes.py              QR + barcode
  hardware.py           cash drawer, cut, feed, density, status, LED
  image.py              upload processing (threshold/dither)
  led.py                LED protocols
  render.py             PIL-based rich-text rasterizer
  text.py               plain-text composer
  widgets.py            quote/joke/haiku/weather/dice/...
auth/
  db.py                 SQLite (users + messages, werkzeug password hashing)
  session.py            require_allowed / require_admin decorators
  blueprint.py          /api/m/auth/{register,login,logout} + /me
templates/
  index.html            main GUI (tabs)
  friends.html          public friends page
static/
  app.js / style.css    main GUI
  friends.js / .css     friends page
scripts/
  test_auth_flow.py     Flask-test-client smoke test
Dockerfile              python:3.12-slim + libusb + DejaVu fonts
docker-compose.yml      privileged + /dev/bus/usb + ./data volume
DEPLOY.md               NAS run-book
.env.example            what to fill into .env on the NAS
```

## Configuration

Everything lives in env vars with dev defaults. Copy
[`.env.example`](.env.example) to `.env` on the NAS and fill in the
required values (SECRET_KEY, ADMIN_TOKEN).

The printer is assumed to be a USB device with VID `0x0483` / PID
`0x5720`. Change `USB_VENDOR_ID` / `USB_PRODUCT_ID` in the env if yours
is different.
