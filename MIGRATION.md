# Migrating from Tailscale Funnel to Cloudflare + the TOTP login — one-time cutover

This is the runbook for moving an already-running Pi from the old world
(entire app funneled at `https://thermal-printer.<tailnet>.ts.net/`)
straight to the current one: everything at `https://print.cuzeth.com` —
friends on the front page, owner console at `/admin` behind a 6-digit
TOTP code. No Tailscale, no Cloudflare Access, no Zero Trust dashboard;
one tunnel, one hostname, and the app defends itself.

(The repo briefly had an intermediate design — two hostnames with the
console behind Cloudflare Access. It was never deployed. If your Pi is
still on the Funnel, that world never existed for you; this runbook
skips it entirely.)

What to expect:

- Friends are offline from step 2 until step 7. That's fine — it's a
  receipt printer, not a pacemaker.
- Nothing about the printer, the database, or friend accounts changes.
  Friends keep their usernames and history; they sign in once more on
  the new domain (session cookies don't follow domains).
- Budget about an hour. The one genuinely slow thing (step 0) can take
  a day or two, so do it ahead of time.

For a from-scratch install (new Pi, no history), ignore this file and
follow [DEPLOY.md](DEPLOY.md). Troubleshooting for every step lives in
DEPLOY.md's "Common stumbles" table.

---

## 0 · Days before: get cuzeth.com onto Cloudflare

Skip if the domain's DNS is already on Cloudflare. Otherwise: add the
site in the [Cloudflare dashboard](https://dash.cloudflare.com) (Free
plan), then switch the nameservers at your registrar to the pair
Cloudflare assigns you. Propagation can take up to a day or two —
everything else in this runbook waits on it, nothing else does.

While you wait, make sure this change is merged to `main` and pushed,
so the Pi has something to pull.

---

## 1 · SSH in — from your home network

```sh
ssh pi@thermal-printer.local
```

**Do not do this migration over a Tailscale SSH session.** Step 2 tears
down Tailscale's proxy and step 8 may remove Tailscale entirely; if
your SSH connection rides the tailnet, you'll saw off the branch you're
sitting on. `.local` mDNS from the same Wi-Fi is the safe path.

Before touching anything, note the commit the Pi is currently running —
it's your rollback target:

```sh
cd ~/thermal-printer
git rev-parse --short HEAD    # write this down
```

---

## 2 · Take the old door down

```sh
sudo tailscale funnel --https=443 off   # stop serving the internet
sudo tailscale serve reset              # wipe the whole serve config, funnel included
sudo systemctl stop thermal-printer     # stop the app while we work on it
```

The ts.net URL is now dead for everyone, you included. Onward.

---

## 3 · Back up what matters

Two things hold state: the SQLite database (friends + message history)
and `.env` (secrets). The migration shouldn't touch either, but copies
are free:

```sh
cd ~/thermal-printer
tar czf ~/tp-backup-$(date +%F).tgz data .env
```

---

## 4 · Pull the new code

```sh
cd ~/thermal-printer
git pull
```

No new Python dependencies — skip pip. (The TOTP implementation is
stdlib; the QR in the setup script reuses the `qrcode` you already
have.) If you want the paranoid version anyway:
`.venv/bin/pip install -r requirements.txt` is harmless.

---

## 5 · Mint the TOTP secret and update `.env`

```sh
.venv/bin/python scripts/gen_totp.py
```

Scan the QR it prints with your authenticator app (Google
Authenticator, Apple Passwords, Aegis, 1Password, ...) **before
closing the terminal** — that app entry is now the key to your
console. Then edit `.env`:

1. **Add** the `TOTP_SECRET=...` line the script printed.
2. **Delete** any `DEV_BYPASS_TAILNET` line if one exists. The variable
   is long gone. Today's dev bypass, `DEV_BYPASS_ADMIN`, must never
   appear in this file either.

`SECRET_KEY` and `ADMIN_TOKEN` stay exactly as they are. Don't start
the service yet — there's no door for it until the tunnel is up.

---

## 6 · Install cloudflared and create the tunnel

Install from Cloudflare's apt repo (arm64 builds included):

```sh
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
  sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | \
  sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared
```

Create the tunnel (the login prints a URL — open it on your Mac, pick
the `cuzeth.com` zone):

```sh
cloudflared tunnel login
cloudflared tunnel create thermal-printer
```

Note the tunnel UUID it prints (also the filename of
`~/.cloudflared/<UUID>.json`). Then point the one hostname at it:

```sh
cloudflared tunnel route dns thermal-printer print.cuzeth.com
```

Install the repo's ingress config and run cloudflared as a service:

```sh
sudo mkdir -p /etc/cloudflared
sudo cp ~/thermal-printer/deploy/cloudflared-config.yml /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml   # fill in TUNNEL_ID (both places)
sudo cloudflared --config /etc/cloudflared/config.yml service install
sudo systemctl start cloudflared
sudo systemctl status cloudflared      # "active (running)"
```

That's the whole Cloudflare setup — one DNS record, one config file.
No Access application, no Zero Trust team, no Transform Rules.

---

## 7 · Start the app and verify everything

```sh
sudo systemctl start thermal-printer
curl http://localhost:5005/api/ping     # {"ok": true, ...}
```

Now the full drill:

1. **Friends** (phone on cellular): `https://print.cuzeth.com/` →
   friends page loads with CSS/JS;
   `https://print.cuzeth.com/api/ping` → `{"ok": true}`.
2. **Console** (any device, any network — this is the point):
   `https://print.cuzeth.com/admin` → code prompt → the 6 digits from
   your authenticator → full GUI loads. Print something.
3. **Stranger's view** (same phone):
   ```sh
   curl -si https://print.cuzeth.com/api/admin/users | head -1
   # HTTP/2 401  ← every /api/admin route demands the TOTP session or
   # the Bearer token; the login page at /admin is all they get.
   ```
4. **Old world is dead**: `https://thermal-printer.<tailnet>.ts.net/`
   fails to connect at all.
5. **End to end**: sign in as a friend on the new domain, print
   something, watch paper come out.

---

## 8 · Retire Tailscale

The app no longer reads anything Tailscale-shaped; the proxy config is
already wiped (step 2). If the only reason Tailscale was on the Pi was
this app, remove it:

```sh
sudo apt-get remove tailscale
```

**Keep it** if you still want to SSH into the Pi from outside your
apartment — that's the one job it still does, and nothing in the new
setup conflicts with it.

---

## 9 · Tell the friends

Send everyone the new URL: `https://print.cuzeth.com/`. Same username,
same password, same history — they just have to sign in again, because
the old domain's session cookie can't follow them.

Done. You can delete this file once the paper is flowing — the
steady-state runbook is [DEPLOY.md](DEPLOY.md).

---

## If it goes sideways: rollback

To go back to the old world at any point, check out the commit you
wrote down in step 1:

```sh
cd ~/thermal-printer
git checkout <that commit>
sudo systemctl restart thermal-printer
sudo systemctl stop cloudflared         # if it got as far as starting
sudo tailscale funnel --bg 5005         # reopen the old door
```

`data/` is untouched by checkout, so friends and history survive the
round trip. When you're ready to retry, `git checkout main` and start
again from step 2.
