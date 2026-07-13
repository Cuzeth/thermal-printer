# Deploying to a Raspberry Pi — the slow, friendly version

Target hardware: a **Raspberry Pi Zero 2W** (but any Pi works) running
**Raspberry Pi OS Lite (64-bit)**. The printer plugs into the Pi over
USB; one Cloudflare Tunnel carries everything at
`https://print.cuzeth.com`. Friends land on the front page; you unlock
the console at `/admin` with a 6-digit code from your authenticator
app (no VPN, no client software, no Cloudflare dashboard ceremony).
No port forwarding anywhere — the tunnel is an outbound-only connection
from the Pi, so this works from an apartment with untouchable router
settings.

End state:

- Friends page (`/`) and its API — public at `https://print.cuzeth.com`.
- Owner console (`/admin`) and its API (`/api/admin/*`) — same
  hostname, gated by the app itself: a TOTP login (secret in `.env`,
  enrolled in your authenticator app) with rate-limited attempts and
  single-use codes. `/api/admin/*` also accepts the Bearer
  `ADMIN_TOKEN` for curl.
- Runs as two `systemd` services (`thermal-printer`, `cloudflared`).
  Survives reboots. Logs with `journalctl`.

Work top-to-bottom. Nothing clever, just a lot of small steps.

---

## 1 · Flash the SD card

**On your Mac**, use [Raspberry Pi Imager](https://www.raspberrypi.com/software/).

1. Choose the device (Pi Zero 2W).
2. Choose OS → **Raspberry Pi OS Lite (64-bit)**. No desktop needed.
3. Choose storage → your SD card.
4. Before writing, click the **gear icon** for OS customization:
   - **Hostname**: `thermal-printer`
   - **Enable SSH**: yes, "use password authentication" (or paste a
     public key — whatever you prefer).
   - **Username / password**: default to `pi` + a password you'll
     remember. If you pick a different username, swap it in everywhere
     below.
   - **Wi-Fi**: your SSID + password, locale.
5. Write. Eject. Put the card in the Pi. Plug in power.

First boot takes ~90 seconds. When it's done, find it from your Mac:

```sh
ssh pi@thermal-printer.local
```

If `.local` mDNS isn't working, find the IP in your router's admin UI
and SSH to that instead.

---

## 2 · Install system dependencies

Once you're SSH'd in, update the system and grab what the app needs to
build and run:

```sh
sudo apt-get update
sudo apt-get full-upgrade -y

sudo apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    libusb-1.0-0-dev libjpeg-dev zlib1g-dev \
    fonts-dejavu fonts-noto-cjk fonts-noto-core git curl
```

`libusb-1.0-0-dev` is what `pyusb` talks to. `libjpeg-dev` and
`zlib1g-dev` are for Pillow. `fonts-dejavu` (full package, not
`-core`) is what the rich-text renderer draws Latin/symbols/braille
with — `-core` ships a stripped subset and braille art prints as boxes.
`fonts-noto-cjk` gives us Chinese / Japanese / Korean coverage.
`fonts-noto-core` adds Arabic plus the NotoSansSymbols /
NotoSansSymbols2 faces, which cover music symbols, math alphanumerics,
and the miscellaneous astral-plane glyphs friends love to paste
(fermatas, kaomoji parens, etc.). The renderer picks the right font
per character automatically.

---

## 3 · Clone the repo and set up the venv

```sh
cd ~
git clone https://github.com/Cuzeth/thermal-printer.git
cd thermal-printer

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
```

> **Shortcut:** steps 2 + 3 + 4 + 5 are all bundled in
> `deploy/setup.sh` — run `bash deploy/setup.sh` from the repo root
> and skip straight to step 6 (fill in `.env`). The script is
> idempotent; safe to run twice.

---

## 4 · USB permissions (udev rule)

By default, only root can open raw USB devices on Linux. The app runs
as `pi`, so we need a udev rule that marks the printer world-writable:

```sh
sudo install -m 0644 \
    ~/thermal-printer/deploy/99-thermal-printer.rules \
    /etc/udev/rules.d/99-thermal-printer.rules

sudo udevadm control --reload
sudo udevadm trigger
```

The rule matches VID `0x0483` / PID `0x5720` (the default printer in
`config.py`). If your printer reports different IDs, edit the rule
before installing and also set `USB_VENDOR_ID` / `USB_PRODUCT_ID` in
`.env`.

**Verify it took:** plug the printer in and run `lsusb` — you should
see a line with `0483:5720`. Then:

```sh
ls -l /dev/bus/usb/*/* | grep -i '0483\|ID'
```

Any entry for your printer bus should show `crw-rw-rw-` (world-writable).
If it's `crw-------`, unplug / replug the printer so the rule re-fires.

---

## 5 · systemd service

Install the unit:

```sh
sudo install -m 0644 \
    ~/thermal-printer/deploy/thermal-printer.service \
    /etc/systemd/system/thermal-printer.service

sudo systemctl daemon-reload
sudo systemctl enable thermal-printer.service
```

The unit is already pointed at `/home/pi/thermal-printer` and runs as
the `pi` user. If your username is different, open
`/etc/systemd/system/thermal-printer.service` and replace `pi`
everywhere — or just re-run `bash deploy/setup.sh`, which templates
the paths for the invoking user.

Don't start it yet — it'll crash without `.env`.

---

## 6 · Fill in `.env`

```sh
cd ~/thermal-printer
cp .env.example .env
```

Generate the three required secrets:

```sh
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
python3 -c "import secrets; print('ADMIN_TOKEN=' + secrets.token_urlsafe(32))"
.venv/bin/python scripts/gen_totp.py
```

Paste each full line into `.env`, replacing the blank placeholders.
`gen_totp.py` prints a QR code — scan it with any authenticator app
(Google Authenticator, Apple Passwords, Aegis, 1Password, ...) **before
you close the terminal**, then paste its `TOTP_SECRET=` line into
`.env`. That app entry is now the key to your console; the 6-digit
code it shows is what `/admin` asks for. The rest of `.env.example` is
just documented defaults — leave them alone unless you know you need
to override something.

> `ADMIN_TOKEN` is for scripts (`curl -H "Authorization: Bearer ..."`
> against `/api/admin/*`) — the browser console never needs it. Keep
> both secrets secret; either one is full console access.

You **don't** need to set `COOKIE_SECURE` — it defaults to `true`,
which is correct for the Pi (print.cuzeth.com is HTTPS). Only set it
to `false` for local HTTP dev on your Mac.

---

## 7 · Start the service

```sh
sudo systemctl start thermal-printer
sudo systemctl status thermal-printer    # should say "active (running)"
```

Tail the logs:

```sh
journalctl -u thermal-printer -f
# Ctrl+C to stop watching; the service keeps running.
```

You should see the startup banner:

```
Thermal Printer GUI -> http://127.0.0.1:5005
```

Smoke test from the Pi itself:

```sh
curl http://localhost:5005/api/ping
# {"ok": true, "dry_run": false}
```

If the service is crashing, `journalctl -u thermal-printer -n 100`
shows the traceback. Most common issue: `.env` has a typo or a missing
`SECRET_KEY`.

---

## 8 · Cloudflare Tunnel (the only door)

Everything is served by `cloudflared` — an outbound-only connector to
Cloudflare's edge that proxies requests to `127.0.0.1:5005`. One
hostname, no edge auth, no path allowlist: the app defends itself
(friend sessions, the TOTP login, and rate limits all live in Flask).

Prerequisite: `cuzeth.com` is on Cloudflare (free plan) — the tunnel
can only route hostnames whose DNS Cloudflare controls.

**8.1 · Install cloudflared** (Cloudflare's apt repo, arm64 builds
included):

```sh
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
  sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | \
  sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared
```

**8.2 · Create the tunnel.** The login prints a URL — open it on your
Mac and pick the `cuzeth.com` zone:

```sh
cloudflared tunnel login
cloudflared tunnel create thermal-printer
```

`create` prints a tunnel UUID and writes a credentials file to
`~/.cloudflared/<UUID>.json`. Note both.

**8.3 · Point DNS at the tunnel** (creates a proxied CNAME):

```sh
cloudflared tunnel route dns thermal-printer print.cuzeth.com
```

**8.4 · Install the repo's ingress config.** The file in the repo is
the source of truth:

```sh
sudo mkdir -p /etc/cloudflared
sudo cp ~/thermal-printer/deploy/cloudflared-config.yml /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml
# replace TUNNEL_ID (both places)
```

If your username isn't `pi`, also fix the home dir in
`credentials-file`. Then run it as a service:

```sh
sudo cloudflared --config /etc/cloudflared/config.yml service install
sudo systemctl start cloudflared
sudo systemctl status cloudflared      # "active (running)"
```

That's the whole edge setup — no Zero Trust dashboard, no Access
application, no Transform Rules.

**Migrating a Pi that's still running the old Tailscale Funnel
setup?** Don't improvise from this section — follow
[MIGRATION.md](MIGRATION.md), the start-to-finish cutover runbook
(teardown order, backups, verification, rollback).

---

## 9 · Verify it works

1. **Local** (SSH'd in on the Pi):
   ```sh
   curl http://localhost:5005/api/ping
   # {"ok": true, "dry_run": false}
   ```

2. **Console** (any device, any network — this is the point):
   - `https://print.cuzeth.com/admin` → the code prompt → type the
     6 digits from your authenticator app → full GUI loads.
   - Wrong code → "wrong code"; ten wrong codes → locked out for 15
     minutes (restarting the service clears it early).

3. **Public** (phone on cellular):
   - `https://print.cuzeth.com/` → friends page loads with CSS/JS.
   - `https://print.cuzeth.com/api/ping` → `{"ok": true}`.
   - `https://print.cuzeth.com/m/` → redirects to `/` (old bookmarks
     survive).

4. **The anonymous-console drill** (same public device):
   ```sh
   curl -si https://print.cuzeth.com/api/admin/users | head -1
   # HTTP/2 401  ← every /api/admin route demands the TOTP session or
   # the Bearer token; the login page at /admin is all a stranger gets.
   ```

---

## 10 · Use it

1. Open `https://print.cuzeth.com/` on your phone, tap **Create an
   account**, username + password (8+ chars), submit.
2. On your laptop, open `https://print.cuzeth.com/admin` → enter your
   code → **Admin** tab → see yourself in **Pending** → **Approve**.
3. Back on the phone → tap **Check again** → state flips to ALLOWED.
4. Type a message → **Print it** → paper comes out.

Share `https://print.cuzeth.com/` with whoever you want to let in.

---

## Updating the app

```sh
ssh pi@thermal-printer.local
cd ~/thermal-printer

# pull latest code
git pull

# install any new/changed deps
source .venv/bin/activate
pip install -r requirements.txt
deactivate

# restart
sudo systemctl restart thermal-printer

# verify
sudo systemctl status thermal-printer    # "active (running)"
journalctl -u thermal-printer -n 10      # no tracebacks
curl http://localhost:5005/api/ping       # {"ok": true}
```

`data/` is inside the repo but gitignored, so the SQLite DB (users +
messages) and any DRY_RUN bytes survive `git pull`.

**If you need to reconfigure the tunnel** (unlikely after initial
setup) — re-sync the config from the repo and restart:

```sh
sudo cp ~/thermal-printer/deploy/cloudflared-config.yml /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml    # re-fill TUNNEL_ID (both places)
sudo systemctl restart cloudflared
```

**One-liner** for quick pulls when nothing changed in `.env` or deps:

```sh
cd ~/thermal-printer && git pull && sudo systemctl restart thermal-printer
```

---

## Common stumbles

| Symptom | Usual cause / fix |
| --- | --- |
| `Could not connect to printer` on every print | udev rule didn't apply. Unplug/replug the printer, or re-run `sudo udevadm trigger`. Check `ls -l /dev/bus/usb/...` — expect `crw-rw-rw-`. |
| Service crashes on start with `USBError: Access denied` | Same as above — the app is running as `pi` but the device is still root-only. |
| `systemctl status` shows `failed` with `FileNotFoundError: .env` | `.env` missing or at wrong path. `EnvironmentFile=` in the unit must point at `/home/pi/thermal-printer/.env`. |
| print.cuzeth.com returns 502 | The app isn't running (`sudo systemctl status thermal-printer`) — the tunnel is up but has nothing to proxy to. |
| print.cuzeth.com shows a Cloudflare error page (530 / error 1033) | The tunnel itself is down. `sudo systemctl status cloudflared`, then `journalctl -u cloudflared -n 50`. Usual causes: TUNNEL_ID placeholder left in `/etc/cloudflared/config.yml`, or the credentials-file path is wrong. |
| `/admin` says "wrong code" but the code is straight from the app | Clock skew. TOTP compares clocks; the login tolerates ±30s and the Pi normally NTP-syncs, but check `timedatectl` on the Pi ("System clock synchronized: yes") and make sure your phone's clock is on automatic. |
| `/admin` login answers 503 "TOTP_SECRET is not set" | Fresh `.env` without the secret. Run `.venv/bin/python scripts/gen_totp.py`, scan the QR, paste the `TOTP_SECRET=` line into `.env`, restart the service. |
| `/admin` says "too many failed attempts" and you're the one locked out | The TOTP lockout tripped (10/15min per IP, 30 global — someone may be poking at it). Wait 15 minutes or `sudo systemctl restart thermal-printer`. The Bearer `ADMIN_TOKEN` keeps working against `/api/admin/*` during a lockout. |
| `/admin` says "code already used" | Codes are single-use by design. Wait ~30s for the next one. |
| Friend messages print as rows of boxes / blank where CJK, Arabic, braille, or music symbols should be | Missing font glyphs on the Pi. Install the full set: `sudo apt-get install -y fonts-dejavu fonts-noto-cjk fonts-noto-core && sudo systemctl restart thermal-printer`. The renderer picks fonts per character; `fonts-noto-cjk` covers CJK, `fonts-noto-core` covers Arabic + Noto Sans Symbols(2) for music/math/misc, and the full `fonts-dejavu` (not `-core`) covers braille. |
| Arabic prints but reads left-to-right | `arabic-reshaper` / `python-bidi` weren't installed. `.venv/bin/pip install -r requirements.txt && sudo systemctl restart thermal-printer`. The renderer detects RTL runs and applies UAX #9 before drawing; without those libs it falls back to logical-order output. |
| Friend login stuck at `429 too many failed attempts` | Rate limit tripped. `sudo systemctl restart thermal-printer` clears it, or wait 15 minutes. |
| `register` 500s with "no column named password_hash" | Old `data/app.db` from a pre-password schema. `init()` doesn't migrate that far back — move it aside (`mv data/app.db data/app.db.bak`) and restart; a fresh DB is created on boot. |
| Friends can sign in but every print/action says "not signed in" | Session cookie not sticking. On the Pi this shouldn't happen (`COOKIE_SECURE` defaults to `true` and print.cuzeth.com is HTTPS). In local dev, run `COOKIE_SECURE=false python3 app.py`. |
| Hostname `thermal-printer.local` doesn't resolve on Mac | mDNS flaky on some networks. Find the Pi's IP another way — `ping thermal-printer.local` from a phone app, an ARP scan (`arp -a`), or plug in a screen and run `hostname -I` — and SSH to that. |

---

## Shutting down and starting up

**Shut down cleanly:**

```sh
sudo systemctl stop thermal-printer
sudo shutdown now
```

**Start up:** just plug in power. Everything survives reboots
automatically — `thermal-printer.service` and `cloudflared.service` are
both `enabled` in systemd. Verify:

```sh
sudo systemctl status thermal-printer    # "active (running)"
sudo systemctl status cloudflared        # "active (running)"
```

If the service didn't come up (e.g. after a power cut), start it
manually:

```sh
sudo systemctl start thermal-printer
```

---

## Cheat sheet

```sh
# services
sudo systemctl status thermal-printer
sudo systemctl restart thermal-printer
journalctl -u thermal-printer -f
sudo systemctl status cloudflared
journalctl -u cloudflared -f

# shutdown / startup
sudo systemctl stop thermal-printer && sudo shutdown now   # off
sudo systemctl status thermal-printer                      # verify after boot

# quick update (no dep changes)
cd ~/thermal-printer && git pull && sudo systemctl restart thermal-printer

# full update (safe default)
cd ~/thermal-printer && git pull && \
  .venv/bin/pip install -r requirements.txt && \
  sudo systemctl restart thermal-printer

# re-sync the tunnel ingress from the repo
sudo cp ~/thermal-printer/deploy/cloudflared-config.yml /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml   # re-fill TUNNEL_ID (both places)
sudo systemctl restart cloudflared
```

That's it. Deep breath. You're running a Flask app as a systemd
service on a Raspberry Pi now.
