# Deploying to your Synology NAS — the slow, friendly version

You're going to do **six things** in order:

1. **Build** the app into a Docker image on your Mac.
2. **Move** that image to your NAS.
3. **Set up** the NAS (enable SSH, install Container Manager, put files in place, fill in secrets).
4. **Plug in** the printer.
5. **Start** the container.
6. **Open it up** through Tailscale (private main GUI + public friends page).

None of this is clever. It's just a lot of small steps. Work top-to-bottom;
if something looks weird, scroll up and re-read rather than improvise.

---

## 1 · Build the image on your Mac

**What a Docker image is:** one self-contained file that bundles your
code + Python + every library it needs. The NAS runs that file without
caring what's installed on it.

**You'll need:** [OrbStack](https://orbstack.dev) or Docker Desktop
running on your Mac (OrbStack is lighter). Open it and make sure the
whale/penguin icon in your menu bar is solid, not spinning.

From the `thermal-printer/` folder on your Mac:

```sh
docker buildx build --platform linux/amd64 -t thermal-printer:latest --load .
```

Breakdown:

- `--platform linux/amd64` — your Mac might be an M-series (ARM) chip
  but the NAS is Intel. This flag tells Docker to build an Intel image
  anyway.
- `-t thermal-printer:latest` — names the image.
- `--load .` — build from the current folder, load the result into your
  local Docker.

First build takes ~3 min (downloading Python, installing libs). After
that it's closer to 15 s.

**How to know it worked:**

```sh
docker images | grep thermal-printer
# thermal-printer   latest   abc123def456   2 minutes ago   380MB
```

---

## 2 · Move the image to the NAS

Two ways, pick one.

### Option A — the NAS mounted in Finder (recommended, no SSH yet)

1. In Finder, press **⌘K** (Go → Connect to Server).
2. Type `smb://your-nas.local` (or the NAS's IP address).
3. Sign in with your DSM username + password.
4. Mount the `docker` share. If you don't have one yet, make it in DSM:
   **Control Panel → Shared Folder → Create → name it `docker`, put it
   on `volume1`**.
5. On your Mac, save the image directly onto the mounted share:

   ```sh
   mkdir -p /Volumes/docker/thermal-printer
   docker save thermal-printer:latest \
       -o /Volumes/docker/thermal-printer/thermal-printer.tar
   ```

That's it — the ~200 MB file now lives on the NAS at
`/volume1/docker/thermal-printer/thermal-printer.tar`.

### Option B — scp over SSH (if Option A is annoying)

Once you've turned on SSH (step 3a below):

```sh
docker save thermal-printer:latest -o thermal-printer.tar
scp thermal-printer.tar you@your-nas.local:/volume1/docker/thermal-printer/
```

---

## 3 · Set up the NAS

### 3a. Turn on SSH

In DSM (your NAS's web UI):

1. **Control Panel → Terminal & SNMP → Enable SSH service → Apply**.
2. From your Mac, test it: `ssh your-dsm-username@your-nas.local`. You
   should land in a shell. `exit` to come back.

### 3b. Install Container Manager

This is Synology's friendly name for Docker.

1. DSM → **Package Center** → search "Container Manager" → **Install**.
2. Wait a minute. The icon appears on the DSM desktop when it's ready.

### 3c. Drop the project files next to the image tarball

You need `docker-compose.yml` and an `.env` file in the same folder as
`thermal-printer.tar`. With the NAS mounted on your Mac:

```sh
# on your Mac, still in the thermal-printer/ repo folder
cp docker-compose.yml /Volumes/docker/thermal-printer/
cp .env.example        /Volumes/docker/thermal-printer/.env
mkdir -p               /Volumes/docker/thermal-printer/data
```

The `data/` folder is where the SQLite database (users + messages) and
DRY_RUN bytes will live. It persists across container restarts.

### 3d. Fill in `.env`

Open `/Volumes/docker/thermal-printer/.env` in any text editor. You
need to set two things:

```
SECRET_KEY=<paste 64-char hex here>
ADMIN_TOKEN=<paste 43-char url-safe string here>
```

Generate both on your Mac:

```sh
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
python3 -c "import secrets; print('ADMIN_TOKEN=' + secrets.token_urlsafe(32))"
```

Paste the full output lines into `.env`, replacing the empty
placeholders.

> You don't need to memorize `ADMIN_TOKEN`. The main GUI reads it from
> the env and inlines it in the page automatically. Keep it secret —
> anyone with it can approve/block friends.

---

## 4 · Plug in the printer

1. Plug the printer's USB-B cable into any USB port on the NAS.
2. Power-cycle the printer (off/on) just to be safe.
3. SSH into the NAS:
   ```sh
   ssh you@your-nas.local
   ```
4. Check Linux is seeing USB devices:
   ```sh
   ls /dev/bus/usb/
   # You should see numbered directories like  001  002
   ```

   If those folders exist, the kernel has a USB stack and the
   printer is visible somewhere inside them. The container mounts the
   whole `/dev/bus/usb` tree, so it doesn't matter which bus number
   your printer gets.

---

## 5 · Start the container

Still SSH'd into the NAS:

```sh
cd /volume1/docker/thermal-printer

# Load the image from the tarball into Docker
sudo docker load -i thermal-printer.tar
# -> Loaded image: thermal-printer:latest

# Start the container (detached, restarts on boot)
sudo docker compose up -d
```

**`sudo` is required** because the container needs `--privileged` +
USB device mounts, which DSM only allows for root.

**Check it's alive:**

```sh
curl http://localhost:5005/api/ping
# -> {"ok": true, "dry_run": false}
```

**See live logs** (useful when something is wrong):

```sh
sudo docker compose logs -f
# Ctrl+C to detach; the container keeps running
```

Right now the app is reachable at `http://your-nas.local:5005` from any
device on your home network. That's enough to print a test page — try
opening that URL in your Mac's browser. Admin tab works, Compose
works, Widgets work.

But the whole point is accessing it from outside the house, which is
the next part.

---

## 6 · Open it up through Tailscale

Tailscale gives you two things here:

- **`tailscale serve`** — makes the main GUI reachable from any of your
  own devices (your phone, your laptop, your iPad) on your tailnet, no
  matter where they are. Not reachable from the public internet.
- **`tailscale funnel`** — exposes exactly the `/m/` path on the public
  internet, with a real HTTPS cert, so friends can send you messages.

### 6a. Install Tailscale on the NAS

1. DSM → **Package Center** → search "Tailscale" → **Install**.
2. Open the Tailscale app in DSM → **Sign in**. Complete the sign-in
   flow in the popup — this enrolls the NAS onto your tailnet.

### 6b. One-time outbound routing setup

This lets other apps (specifically Serve + Funnel) send traffic
through Tailscale:

```sh
sudo /var/packages/Tailscale/target/bin/tailscale set --advertise-routes=
sudo /var/packages/Tailscale/target/bin/tailscale up
```

### 6c. Find your NAS's tailnet hostname

Open the [Tailscale admin console →
Machines](https://login.tailscale.com/admin/machines). Find the NAS in
the list. Its hostname looks like:

```
your-nas.tail-XXXX.ts.net
```

(The `tail-XXXX` slug is unique to your tailnet.) Copy that down.

### 6d. Make the main GUI reachable on your tailnet

Back on the NAS via SSH:

```sh
TS=/var/packages/Tailscale/target/bin/tailscale
sudo $TS serve --bg --set-path=/ http://localhost:5005
```

From any device on your tailnet (your Mac, your iPhone with the
Tailscale app running) open:

```
https://your-nas.tail-XXXX.ts.net/
```

That's the full GUI, with a real TLS cert, reachable from anywhere via
Tailscale.

### 6e. Expose `/m/` publicly via Funnel

First, two clicks in the Tailscale admin console:

1. [DNS settings](https://login.tailscale.com/admin/dns) — make sure
   **MagicDNS** is on and **HTTPS Certificates** is enabled.
2. [ACL editor](https://login.tailscale.com/admin/acls) — in the
   `nodeAttrs` block, grant `"funnel"` to this device (or to a tag it
   has). Save.

Then on the NAS:

```sh
sudo $TS funnel --bg --set-path=/m http://localhost:5005/m
```

Now `https://your-nas.tail-XXXX.ts.net/m/` works from the open
internet. That's the URL you share with friends.

**Sanity check:**

- From your phone on cellular (tailnet OFF): open `/m/` → friends
  page loads. Open `/` → blocked (502 or timeout). 
- From your laptop on the tailnet: both work.

---

## You're done. Now use it.

1. Open the friends URL on your phone, tap **Create an account**, pick
   a username + password (8+ chars), submit.
2. From your Mac → open the main GUI URL → click the **Admin** tab →
   see yourself in **Pending** → click **Approve**.
3. Back on your phone → tap **Check again** → state flips to ALLOWED.
4. Type a message → **Print it** → the printer hums and spits out a
   receipt with your name at the top.

Share the friends URL with whoever you want to let in.

---

## Updating later

Anytime you change code on your Mac:

```sh
# 1. Rebuild on your Mac
docker buildx build --platform linux/amd64 -t thermal-printer:latest --load .

# 2. Save the new image straight to the mounted share
docker save thermal-printer:latest \
    -o /Volumes/docker/thermal-printer/thermal-printer.tar

# 3. Reload + restart on the NAS
ssh you@your-nas.local
cd /volume1/docker/thermal-printer
sudo docker load -i thermal-printer.tar
sudo docker compose up -d --force-recreate
```

`data/` survives restarts. Approved friends and their passwords stick.

---

## Common stumbles

| Symptom | Usual cause / fix |
| --- | --- |
| `permission denied` on `docker` commands | Missing `sudo`. DSM requires root for privileged containers + USB. |
| Container starts but "Could not connect to printer" on every print | USB passthrough. `sudo docker exec -it thermal-printer ls /dev/bus/usb/` should show numbered folders. If empty, the container is missing `privileged: true` — check the compose file landed correctly on the NAS. |
| Funnel URL returns 502 | Run `sudo $TS funnel status` — confirm `/m` is listed. If not, re-run step 6e. If it is, make sure the container is up (`sudo docker compose ps`). |
| Login stuck at `429 too many failed attempts` | Rate limit tripped. `sudo docker compose restart` clears it, or wait 15 minutes. |
| Friends page says "couldn't reach the server" | Container isn't running, or port 5005 isn't bound. Check `sudo docker compose logs` for tracebacks. |
| After deploy, `register` 500s with "no column named password_hash" | An old `data/app.db` from a pre-password schema is lying around. The app auto-renames it to `app.db.bak-*` on first boot — just restart the container once. |

---

## Cheat sheet

Commands you'll want to remember, once everything works:

```sh
# Check the container
sudo docker compose ps
sudo docker compose logs -f
sudo docker compose restart

# Update after a code change
sudo docker load -i thermal-printer.tar && sudo docker compose up -d --force-recreate

# Check tailscale wiring
sudo /var/packages/Tailscale/target/bin/tailscale serve status
sudo /var/packages/Tailscale/target/bin/tailscale funnel status
```

That's it. Deep breath. You're running Docker on a NAS now.
