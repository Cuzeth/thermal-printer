# Deploying to a Raspberry Pi — the slow, friendly version

Target hardware: a **Raspberry Pi Zero 2W** (but any Pi works) running
**Raspberry Pi OS Lite (64-bit)**. The printer plugs into the Pi over
USB; the Pi joins your tailnet; friends reach `/m/` at
`https://print.cuzeth.com` through a Cloudflare Tunnel. No port
forwarding anywhere — both doors are outbound-only connections from the
Pi, so this works from an apartment with untouchable router settings.

End state:

- Main GUI (`/`) and private API — tailnet-only at
  `https://thermal-printer.<tailnet>.ts.net/` via `tailscale serve`,
  additionally gated by a Flask decorator that checks for the
  `Tailscale-User-Login` header. Not reachable from the internet at all.
- Friends page (`/m/`) and its API — public at
  `https://print.cuzeth.com` via `cloudflared`, whose ingress allowlists
  only the friend paths.
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

## 3 · Install Tailscale

The official installer script is the easiest path and stays
up-to-date with apt:

```sh
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

`tailscale up` prints a login URL. Open it on your Mac, sign in, and
the Pi shows up in your tailnet as `thermal-printer`. Confirm:

```sh
tailscale ip -4
# e.g. 100.x.y.z
```

We'll wire up `tailscale serve` later, after the app is running.

---

## 4 · Clone the repo and set up the venv

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

> **Shortcut:** steps 2 + 4 + 5 + 6 are all bundled in
> `deploy/setup.sh` — run `bash deploy/setup.sh` from the repo root
> and skip straight to step 7 (fill in `.env`). The script is
> idempotent; safe to run twice.

---

## 5 · USB permissions (udev rule)

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

## 6 · systemd service

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

## 7 · Fill in `.env`

```sh
cd ~/thermal-printer
cp .env.example .env
```

Generate the two required secrets:

```sh
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
python3 -c "import secrets; print('ADMIN_TOKEN=' + secrets.token_urlsafe(32))"
```

Paste each full line into `.env`, replacing the blank placeholders. The
rest of `.env.example` is just documented defaults — leave them alone
unless you know you need to override something.

> You don't need to memorize `ADMIN_TOKEN`. The main GUI reads it from
> the env and inlines it in the page automatically. Keep it secret —
> anyone with it has full console access.

You **don't** need to set `COOKIE_SECURE` — it defaults to `true`,
which is correct for the Pi (print.cuzeth.com and the tailnet console
are both HTTPS). Only set it to `false` for local HTTP dev on your Mac.

---

## 8 · Start the service

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

## 9 · Tailscale serve (owner console, tailnet-only)

One command:

```sh
sudo tailscale serve --bg 5005
```

This publishes the app at `https://thermal-printer.<tailnet>.ts.net/`
with a real HTTPS cert — **to your tailnet only**. Unlike the old
Funnel setup, nothing here is internet-reachable; the public door is
the Cloudflare Tunnel in the next step. Tailscale's proxy injects the
`Tailscale-User-Login` header on every request, which is what
`@require_tailnet` routes check for.

> `serve` requires one-time setup in the Tailscale admin console: make
> sure **MagicDNS** is on and **HTTPS Certificates** is enabled
> ([DNS settings](https://login.tailscale.com/admin/dns)). Tailscale
> will tell you if you're missing either. No Funnel ACL attribute is
> needed anymore.

Check what's wired up:

```sh
tailscale serve status
```

**Migrating from the old Funnel setup?** Clear it first — `serve reset`
wipes the whole serve config, funnel flag included:

```sh
sudo tailscale serve reset
sudo tailscale serve --bg 5005
```

---

## 10 · Cloudflare Tunnel (friends page at print.cuzeth.com)

The friends page is public at `https://print.cuzeth.com`, served by
`cloudflared` — an outbound-only connector to Cloudflare's edge. It
proxies allowed paths to `127.0.0.1:5005`, same as tailscaled does for
the console.

One thing to internalize before wiring it up: **cloudflared forwards
client headers verbatim.** A public visitor can send a forged
`Tailscale-User-Login` header, which Tailscale's proxy would never let
through. Three walls stop that, and you're about to build two of them
(the third, a `CF-Ray` check, already lives in `auth/tailnet.py`):
the tunnel's path allowlist, and an edge rule stripping the header.

Prerequisite: `cuzeth.com` is on Cloudflare (free plan) — the tunnel
can only route hostnames whose DNS Cloudflare controls.

**10.1 · Install cloudflared** (Cloudflare's apt repo, arm64 builds
included):

```sh
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
  sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | \
  sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared
```

**10.2 · Create the tunnel.** Like `tailscale up`, the login prints a
URL — open it on your Mac and pick the `cuzeth.com` zone:

```sh
cloudflared tunnel login
cloudflared tunnel create thermal-printer
```

`create` prints a tunnel UUID and writes a credentials file to
`~/.cloudflared/<UUID>.json`. Note both.

**10.3 · Install the repo's ingress config.** The file in the repo is
the source of truth — its path allowlist is load-bearing (see the
comments inside it):

```sh
sudo mkdir -p /etc/cloudflared
sudo cp ~/thermal-printer/deploy/cloudflared-config.yml /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml   # replace TUNNEL_ID in BOTH places
```

If your username isn't `pi`, also fix the home dir in
`credentials-file`.

**10.4 · Point DNS at the tunnel** (creates the proxied CNAME for
`print.cuzeth.com`):

```sh
cloudflared tunnel route dns thermal-printer print.cuzeth.com
```

**10.5 · Run it as a service:**

```sh
sudo cloudflared --config /etc/cloudflared/config.yml service install
sudo systemctl start cloudflared
sudo systemctl status cloudflared      # "active (running)"
```

**10.6 · Strip the identity header at the edge.** In the Cloudflare
dashboard: `cuzeth.com` zone → **Rules** → **Transform Rules** →
**Modify Request Header** → create rule:

- Name: `strip tailscale identity`
- When: All incoming requests
- Then: **Remove** header `Tailscale-User-Login`

This is wall two. Even if the ingress allowlist ever gets fat-fingered,
a forged identity header dies at the edge — and if *both* walls fail,
`auth/tailnet.py` still refuses tailnet status to anything carrying
`CF-Ray`.

---

## 11 · Verify it works

1. **Local** (SSH'd in on the Pi):
   ```sh
   curl http://localhost:5005/api/ping
   # {"ok": true, "dry_run": false}
   ```

2. **Tailnet** (laptop/phone with Tailscale **on**):
   - `https://thermal-printer.<tailnet>.ts.net/` → full GUI loads, real TLS cert.
   - `https://print.cuzeth.com/m/` → friends page loads (the tunnel
     works from inside the tailnet too).

3. **Public** (phone on cellular, Tailscale **off**):
   - `https://print.cuzeth.com/m/` → friends page loads with CSS/JS.
   - `https://print.cuzeth.com/api/ping` → `{"ok": true}` (health check is allowlisted).
   - `https://print.cuzeth.com/` → **404** (not in the tunnel's path allowlist — the console isn't just gated, it's unreachable).
   - `https://print.cuzeth.com/api/print/text` → **404** (same).
   - `https://thermal-printer.<tailnet>.ts.net/` → connection fails entirely (nothing is funneled anymore).

4. **The header-forgery drill** (same public device) — all three walls
   at once:
   ```sh
   curl -si -H "Tailscale-User-Login: owner@example.com" https://print.cuzeth.com/ | head -1
   # HTTP/2 404  ← the allowlist answered; Flask never saw it. And had it
   # gotten through, the edge rule strips the header and the CF-Ray check
   # in auth/tailnet.py refuses it anyway.
   ```

Find your exact `<tailnet>` slug in the
[admin console → Machines](https://login.tailscale.com/admin/machines).

---

## 12 · Use it

1. Open `https://print.cuzeth.com/m/` on your phone, tap **Create an
   account**, username + password (8+ chars), submit.
2. On your laptop, open `/` → **Admin** tab → see yourself in
   **Pending** → **Approve**.
3. Back on the phone → tap **Check again** → state flips to ALLOWED.
4. Type a message → **Print it** → paper comes out.

Share `https://print.cuzeth.com/m/` with whoever you want to let in.

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

**If you need to reconfigure the proxies** (unlikely after initial
setup):

```sh
# tailnet console: tear down and rebuild
sudo tailscale serve reset
sudo tailscale serve --bg 5005

# cloudflare tunnel: re-sync the config from the repo and restart
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
| Tailnet URL: "couldn't establish a secure connection" / hangs then fails | The ts.net cert hasn't provisioned (or went stale after an idle period). Force a refresh: `sudo tailscale cert thermal-printer.<tailnet>.ts.net`. If still failing, `sudo systemctl restart tailscaled && sudo tailscale serve --bg 5005`. |
| Friend messages print as rows of boxes / blank where CJK, Arabic, braille, or music symbols should be | Missing font glyphs on the Pi. Install the full set: `sudo apt-get install -y fonts-dejavu fonts-noto-cjk fonts-noto-core && sudo systemctl restart thermal-printer`. The renderer picks fonts per character; `fonts-noto-cjk` covers CJK, `fonts-noto-core` covers Arabic + Noto Sans Symbols(2) for music/math/misc, and the full `fonts-dejavu` (not `-core`) covers braille. |
| Arabic prints but reads left-to-right | `arabic-reshaper` / `python-bidi` weren't installed. `.venv/bin/pip install -r requirements.txt && sudo systemctl restart thermal-printer`. The renderer detects RTL runs and applies UAX #9 before drawing; without those libs it falls back to logical-order output. |
| `/` returns 403 even when I'm on the tailnet | Tailscale's identity headers aren't being injected. Check that MagicDNS and HTTPS certificates are enabled, and that you're accessing the app via its `*.ts.net` hostname (not the raw IP). Run `tailscale serve status` to confirm the proxy is running. |
| `/` is reachable at print.cuzeth.com | The tunnel's path allowlist got widened — diff `/etc/cloudflared/config.yml` against `deploy/cloudflared-config.yml` and restart `cloudflared`. Also verify the Transform Rule (strip `Tailscale-User-Login`) still exists in the Cloudflare dashboard, and that gunicorn binds to `127.0.0.1` (`ss -tlnp \| grep 5005`). |
| Login stuck at `429 too many failed attempts` | Rate limit tripped. `sudo systemctl restart thermal-printer` clears it, or wait 15 minutes. |
| `register` 500s with "no column named password_hash" | Old `data/app.db` from a pre-password schema. `init()` doesn't migrate that far back — move it aside (`mv data/app.db data/app.db.bak`) and restart; a fresh DB is created on boot. |
| Friends can sign in but every print/action says "not signed in" | Session cookie not sticking. On the Pi this shouldn't happen (`COOKIE_SECURE` defaults to `true` and print.cuzeth.com is HTTPS). In local dev, run `COOKIE_SECURE=false python3 app.py`. |
| Hostname `thermal-printer.local` doesn't resolve on Mac | mDNS flaky on some routers. Use the IP from `tailscale ip -4` (the tailnet IP always works once Tailscale is up). |

---

## Shutting down and starting up

**Shut down cleanly:**

```sh
sudo systemctl stop thermal-printer
sudo shutdown now
```

**Start up:** just plug in power. Everything survives reboots
automatically — `thermal-printer.service` and `cloudflared.service` are
`enabled` in systemd, and `tailscale serve --bg` persists its config.
Verify:

```sh
sudo systemctl status thermal-printer    # "active (running)"
sudo systemctl status cloudflared        # "active (running)"
tailscale serve status                   # shows the 5005 proxy
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

# tailscale
tailscale status
tailscale serve status

# quick update (no dep changes)
cd ~/thermal-printer && git pull && sudo systemctl restart thermal-printer

# full update (safe default)
cd ~/thermal-printer && git pull && \
  .venv/bin/pip install -r requirements.txt && \
  sudo systemctl restart thermal-printer

# rebuild the tailnet proxy from scratch
sudo tailscale serve reset
sudo tailscale serve --bg 5005

# re-sync the tunnel ingress from the repo
sudo cp ~/thermal-printer/deploy/cloudflared-config.yml /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml   # re-fill TUNNEL_ID
sudo systemctl restart cloudflared
```

That's it. Deep breath. You're running a Flask app as a systemd
service on a Raspberry Pi now.
