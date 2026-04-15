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

## Deploy to a Raspberry Pi

The short version:

1. Flash Raspberry Pi OS Lite (64-bit) to an SD card, set hostname
   `thermal-printer`, enable SSH.
2. On the Pi: install system deps, clone the repo, create a venv, pip
   install requirements. (`bash deploy/setup.sh` does all this.)
3. Copy `.env.example` → `.env`, fill in `SECRET_KEY` + `ADMIN_TOKEN`.
4. Install the udev rule + systemd unit (also handled by `setup.sh`).
5. Install Tailscale (`curl -fsSL https://tailscale.com/install.sh | sh`).
   Use `tailscale serve` for the main GUI (tailnet-only) and
   `tailscale funnel --set-path=/m` for the friends page (public via a
   `*.ts.net` URL).

Full step-by-step, including udev + systemd details and the Tailscale
wiring, lives in [DEPLOY.md](DEPLOY.md).

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
deploy/
  thermal-printer.service   systemd unit (runs gunicorn, workers=1)
  99-thermal-printer.rules  udev rule (USB access for non-root)
  setup.sh                  idempotent Pi bootstrapper
tests/                  pytest suite (render/widgets/image/routes)
.github/workflows/ci.yml    pytest + auth smoke on push/PR
DEPLOY.md               Raspberry Pi run-book
.env.example            what to fill into .env on the Pi
```

## Running the tests

```sh
pip install pytest
pytest                              # render/widgets/image/routes suite
python scripts/test_auth_flow.py    # end-to-end auth
```

## Configuration

Everything lives in env vars with dev defaults. Copy
[`.env.example`](.env.example) to `.env` on the Pi and fill in the
required values (SECRET_KEY, ADMIN_TOKEN).

The printer is assumed to be a USB device with VID `0x0483` / PID
`0x5720`. Change `USB_VENDOR_ID` / `USB_PRODUCT_ID` in the env if yours
is different.
