# Deploying to a Raspberry Pi — the slow, friendly version

Target hardware: a **Raspberry Pi Zero 2W** (but any Pi works) running
**Raspberry Pi OS Lite (64-bit)**. The printer plugs into the Pi over
USB; the Pi joins your tailnet; friends reach `/m/` over Tailscale
Funnel.

End state:

- Main GUI at `https://thermal-printer.<tailnet>.ts.net/` — tailnet-only.
- Friends page at `https://thermal-printer.<tailnet>.ts.net/m/` — public.
- Runs as a `systemd` service. Survives reboots. Logs with `journalctl`.

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
    fonts-dejavu-core git curl
```

`libusb-1.0-0-dev` is what `pyusb` talks to. `libjpeg-dev` and
`zlib1g-dev` are for Pillow. `fonts-dejavu-core` is what the rich-text
renderer draws with — without it the output looks wrong.

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

We'll wire up `serve` / `funnel` later, after the app is running.

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
which is correct for the Pi (Funnel is HTTPS). Only set it to `false`
for local HTTP dev on your Mac.

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
Thermal Printer GUI -> http://0.0.0.0:5005
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

## 9 · Tailscale serve + funnel

Now we expose it. Two knobs:

- **`tailscale serve`** — reverse-proxies the full app on your tailnet
  only (private).
- **`tailscale funnel`** — exposes a specific path to the public
  internet with a real HTTPS cert.

We want the whole GUI on the tailnet, and only `/m/` public.

```sh
# tailnet-only: whole app reachable at https://thermal-printer.<tailnet>.ts.net/
sudo tailscale serve --bg 5005

# public: expose only /m/ via Funnel
sudo tailscale funnel --bg --set-path=/m 5005
```

The first `serve` command makes the full app reachable within your
tailnet at `https://thermal-printer.<tailnet>.ts.net/`. The `funnel`
command adds a public override for `/m` pointing at the same local port.

**Why one path is enough:** the friends page, its static assets, and
its API all live under `/m/`:

| URL | What |
|---|---|
| `/m/` | friends HTML |
| `/m/static/*` | CSS + JS |
| `/m/api/auth/*` | register, login, logout |
| `/m/api/me` | session check |
| `/m/api/print` | send a message |

The main console (`/`), its assets (`/static/*`), and private API
(`/api/*`) stay tailnet-only because they aren't funneled.

> Funnel requires one-time setup in the Tailscale admin console: make
> sure **MagicDNS** is on, **HTTPS Certificates** is enabled
> ([DNS settings](https://login.tailscale.com/admin/dns)), and your
> ACLs grant the `funnel` nodeAttr to this device
> ([ACL editor](https://login.tailscale.com/admin/acls)). Tailscale
> will tell you if you're missing either.

Check what's wired up:

```sh
tailscale serve status
tailscale funnel status
```

---

## 10 · Verify it works

1. **Local** (SSH'd in on the Pi):
   ```sh
   curl http://localhost:5005/api/ping
   # {"ok": true, "dry_run": false}
   ```

2. **Tailnet** (laptop/phone on your tailnet, Tailscale app on):
   Open `https://thermal-printer.<tailnet>.ts.net/` — full GUI, real TLS cert.

3. **Public** (phone on cellular, Tailscale **off**):
   - `https://thermal-printer.<tailnet>.ts.net/m/` → friends page loads.
   - `https://thermal-printer.<tailnet>.ts.net/` → 502 / timeout.
     That's correct. Private stays private.

Find your exact `<tailnet>` slug in the
[admin console → Machines](https://login.tailscale.com/admin/machines).

---

## 11 · Use it

1. Open `/m/` on your phone, tap **Create an account**, username +
   password (8+ chars), submit.
2. On your laptop, open `/` → **Admin** tab → see yourself in
   **Pending** → **Approve**.
3. Back on the phone → tap **Check again** → state flips to ALLOWED.
4. Type a message → **Print it** → paper comes out.

Share the `/m/` URL with whoever you want to let in.

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

**If you changed Tailscale paths** (unlikely after initial setup), tear
down and rebuild:

```sh
sudo tailscale funnel --https=443 off
sudo tailscale serve --bg 5005
sudo tailscale funnel --bg --set-path=/m 5005
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
| Funnel URL returns 502 | Service isn't running (`sudo systemctl status thermal-printer`) or `tailscale funnel status` doesn't list `/m`. Re-run step 9. |
| Login stuck at `429 too many failed attempts` | Rate limit tripped. `sudo systemctl restart thermal-printer` clears it, or wait 15 minutes. |
| `register` 500s with "no column named password_hash" | Old `data/app.db` from a pre-password schema. The app auto-renames it to `app.db.bak-*` on first boot — just `sudo systemctl restart thermal-printer` once. |
| Friends can sign in but every print/action says "not signed in" | Session cookie not sticking. On the Pi this shouldn't happen (`COOKIE_SECURE` defaults to `true` and Funnel is HTTPS). In local dev, run `COOKIE_SECURE=false python3 app.py`. |
| Friends page loads but no CSS/JS (bare HTML) | Funnel path missing. Make sure `tailscale funnel --set-path=/m 5005` is set — everything the friends page needs (static assets, API) lives under `/m/`. |
| Hostname `thermal-printer.local` doesn't resolve on Mac | mDNS flaky on some routers. Use the IP from `tailscale ip -4` (the tailnet IP always works once Tailscale is up). |

---

## Cheat sheet

```sh
# service
sudo systemctl status thermal-printer
sudo systemctl restart thermal-printer
journalctl -u thermal-printer -f

# tailscale
tailscale status
tailscale serve status
tailscale funnel status

# quick update (no dep changes)
cd ~/thermal-printer && git pull && sudo systemctl restart thermal-printer

# full update (safe default)
cd ~/thermal-printer && git pull && \
  .venv/bin/pip install -r requirements.txt && \
  sudo systemctl restart thermal-printer

# rebuild funnel from scratch
sudo tailscale funnel --https=443 off
sudo tailscale serve --bg 5005
sudo tailscale funnel --bg --set-path=/m 5005
```

That's it. Deep breath. You're running a Flask app as a systemd
service on a Raspberry Pi now.
