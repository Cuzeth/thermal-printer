# Deploying to a Synology NAS (DS225+ / DSM 7.2)

End state: the printer is plugged into the NAS, the GUI runs in a Docker
container under Container Manager, the main GUI is reachable on the
Tailnet only, and `/m/` (the friends page) is reachable from the public
internet via Tailscale Funnel.

---

## 0. One-time prep on your Mac

```sh
# Build for amd64 (the DS225+ is Intel J4125)
docker buildx build --platform linux/amd64 -t thermal-printer:latest --load .

# Save the image to a tarball for the NAS
docker save thermal-printer:latest -o thermal-printer.tar
```

Generate two secrets you'll paste into the NAS's `.env`:

```sh
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
python3 -c "import secrets; print('ADMIN_TOKEN=' + secrets.token_urlsafe(32))"
```

---

## 1. Install Container Manager + SSH on the NAS

In DSM:

1. Package Center → install **Container Manager**.
2. Control Panel → Terminal & SNMP → check **Enable SSH service**.
3. Control Panel → User & Group → make sure your account is in the
   `administrators` group (needed for `sudo` on DSM).

---

## 2. Plug in the printer and confirm USB

SSH in:

```sh
ssh you@your-nas.local
# DSM ships busybox `lsusb` — if missing, the device files are still there:
ls /dev/bus/usb/*/
# Look for the printer's bus/device. The vendor ID is 0483 (STMicro).
sudo cat /sys/bus/usb/devices/*/idVendor 2>/dev/null | sort -u
```

If you don't see `0483`, replug and check again. The container uses
`--privileged` + a full-bus mount, so we don't need to pin to a specific
device path — every USB device on every bus is visible inside.

---

## 3. Drop the project files on the NAS

Copy the image tarball + `docker-compose.yml` + a populated `.env` to a
folder under `/volume1/docker/thermal-printer/`. From your Mac:

```sh
scp thermal-printer.tar docker-compose.yml .env you@your-nas.local:/volume1/docker/thermal-printer/
```

Make sure the host data dir exists:

```sh
ssh you@your-nas.local "mkdir -p /volume1/docker/thermal-printer/data"
```

`.env` should look like (filling in the values from step 0):

```
SECRET_KEY=...
ADMIN_TOKEN=...
```

---

## 4. Load + start the container

On the NAS:

```sh
cd /volume1/docker/thermal-printer
sudo docker load -i thermal-printer.tar
sudo docker compose up -d
sudo docker compose logs -f      # ctrl-C to detach; container keeps running
```

Smoke test from the NAS itself:

```sh
curl http://localhost:5005/api/ping
# -> {"ok": true, "dry_run": false}
```

---

## 5. Install + sign in to Tailscale on the NAS

1. Package Center → search "Tailscale" → install the Synology package
   (`https://tailscale.com/kb/1131/synology`).
2. Open the Tailscale app in DSM → Sign in → use your existing Tailscale
   account.
3. To allow Tailscale to forward TCP to the container (i.e. to use
   Serve/Funnel), it needs a TUN device. SSH in and run the one-time setup:

   ```sh
   sudo /var/packages/Tailscale/target/bin/tailscale set --advertise-routes=
   sudo /var/packages/Tailscale/target/bin/tailscale up
   ```

   (Synology's package wraps the binary; the path above is canonical.)

---

## 6. Enable Funnel for this device (one-time, in the Tailscale admin)

1. Visit `https://login.tailscale.com/admin/dns` → ensure **MagicDNS** is on
   and **HTTPS Certificates** is enabled. This gives the device a stable
   `*.ts.net` hostname with a real cert.
2. Visit `https://login.tailscale.com/admin/acls` → make sure your ACL
   includes a `nodeAttrs` block granting `funnel` to this device, e.g.:

   ```hujson
   "nodeAttrs": [
     { "target": ["tag:home"], "attr": ["funnel"] },
   ],
   ```

   (or just to your specific device by name.)

Find the device hostname from the admin panel — something like
`printer-nas.tail-XXXX.ts.net`. That's the URL your friends will use.

---

## 7. Wire serve + funnel routes

On the NAS, with sudo:

```sh
TS=/var/packages/Tailscale/target/bin/tailscale

# Tailnet-only: full GUI on / (default)
sudo $TS serve --bg --set-path=/ http://localhost:5005

# Public via Funnel: only /m/ exposed
sudo $TS funnel --bg --set-path=/m http://localhost:5005/m

# Sanity check what's wired:
sudo $TS serve status
sudo $TS funnel status
```

You should now have:
- `https://printer-nas.tail-XXXX.ts.net/` — works from any tailnet device,
  reaches the full GUI (Compose, Image, ..., Admin).
- `https://printer-nas.tail-XXXX.ts.net/m/` — works from the open
  internet, friends page only.

---

## 8. End-to-end smoke test

1. From a phone on cellular: open
   `https://printer-nas.tail-XXXX.ts.net/m/` → tap **Create an account**
   → pick a username + password → submit.
2. From your Mac (on the tailnet): open the main GUI →
   **Admin** tab → see the pending user → click **Approve**.
3. Back on the phone: tap **Check again** → state flips to ALLOWED →
   type a message → tap **Print it** → printer prints with `## from <name>`
   header and a timestamp.
4. Try sending again immediately — UI should toast "slow down" (rate
   limited, 10s).
5. Pull the USB cable → send a message → toast shows a printer error.
   Reconnect, retry, succeeds. (The container does NOT need to restart.)

---

## 9. Updating later

When you make changes on the Mac:

```sh
docker buildx build --platform linux/amd64 -t thermal-printer:latest --load .
docker save thermal-printer:latest -o thermal-printer.tar
scp thermal-printer.tar you@your-nas.local:/volume1/docker/thermal-printer/

ssh you@your-nas.local
cd /volume1/docker/thermal-printer
sudo docker load -i thermal-printer.tar
sudo docker compose up -d --force-recreate
```

The `data/` volume (SQLite + DRY_RUN bytes) survives across recreates, so
approved friends and their passkeys stick.

---

## Troubleshooting

**Container can't see the printer (`PrinterError: Could not connect`)**
- Confirm `privileged: true` and the `/dev/bus/usb` mount in
  `docker-compose.yml`. DSM's Container Manager GUI hides both —
  always start the stack from the CLI with `docker compose`.
- `sudo docker exec -it thermal-printer ls /dev/bus/usb/` should show
  a populated bus dir.

**Funnel returns "no service"**
- `sudo $TS funnel status` — confirm `/m` is listed.
- DSM firewall: Control Panel → Security → Firewall → make sure ports
  443 and 5005 aren't blocked on the `tailscale0` interface. Default
  policy on most installs is "allow all" until you create rules.

**Login returns 429 "too many failed attempts"**
- Default lock window is 10 failed attempts per 15 minutes per username
  (in-memory, per-container). Restart the container to clear, or just
  wait it out.

**"can't connect to the server" toast on the friends page**
- From the NAS: `curl http://localhost:5005/m/` should return HTML. If
  not, `sudo docker compose logs printer` for the trace.
