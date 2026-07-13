# Migrating from Tailscale Funnel to Cloudflare — one-time cutover

This is the runbook for moving an already-running Pi from the old world
(entire app funneled at `https://thermal-printer.<tailnet>.ts.net/`) to
the new one (friends at `https://print.cuzeth.com`, owner console at
`https://console.cuzeth.com` behind Cloudflare Access, no Tailscale).

What to expect:

- Friends are offline from step 2 until step 9. That's fine — it's a
  receipt printer, not a pacemaker.
- Nothing about the printer, the database, or friend accounts changes.
  Friends keep their usernames and history; they just sign in once more
  on the new domain (session cookies don't follow domains).
- Budget about an hour, most of it clicking around the Cloudflare
  dashboard. The one genuinely slow thing (step 0) can take a day or
  two, so do it ahead of time.

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

While you wait, merge the migration branch and push, so the Pi has
something to pull:

```sh
# on your Mac
git checkout main && git merge cloudflare-domain && git push
```

---

## 1 · SSH in — from your home network

```sh
ssh pi@thermal-printer.local
```

**Do not do this migration over a Tailscale SSH session.** Step 2 tears
down Tailscale's proxy and step 10 may remove Tailscale entirely; if
your SSH connection rides the tailnet, you'll saw off the branch you're
sitting on. `.local` mDNS from the same Wi-Fi is the safe path.

---

## 2 · Take the old doors down

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

No new Python dependencies in this migration — skip pip. If you want
the paranoid version anyway: `.venv/bin/pip install -r requirements.txt`
is harmless.

---

## 5 · Update `.env`

Open `.env` and:

1. **Add** `OWNER_EMAIL=` set to the email address you'll log into
   Cloudflare Access with (step 7). The app pins the authenticated
   identity to this; with it unset, every private route 403s — the
   console fails closed, by design.
2. **Delete** any `DEV_BYPASS_TAILNET` line if one exists. The variable
   is gone. Its replacement, `DEV_BYPASS_ACCESS`, must never appear in
   this file.

Don't start the service yet — the new gate has nothing to let through
until Cloudflare is wired up.

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
`~/.cloudflared/<UUID>.json`). Then point both hostnames at the tunnel:

```sh
cloudflared tunnel route dns thermal-printer print.cuzeth.com
cloudflared tunnel route dns thermal-printer console.cuzeth.com
```

---

## 7 · Create the Access application

This is what replaces the tailnet. In
[one.dash.cloudflare.com](https://one.dash.cloudflare.com):

1. First visit only: pick a **team name** when Zero Trust prompts you
   (Free plan, 50 users, you need 1). Note the slug.
2. **Access** → **Applications** → **Add an application** →
   **Self-hosted**. Domain: `console.cuzeth.com` — whole hostname, no
   path. Session duration: 1 week is a sane default.
3. Add a policy: Action **Allow**, Include → **Emails** → the same
   address you put in `OWNER_EMAIL`. Nobody else.
4. Leave the default **One-time PIN** login method on (that's the
   emailed code; SSO can come later).
5. On the application's overview, copy the **Application Audience
   (AUD) tag**.

You now hold three values: tunnel UUID, team slug, AUD tag.

---

## 8 · Install the ingress config and start the tunnel

The repo file is the source of truth — its friend-path allowlist and
its console `access` block are both load-bearing (comments inside
explain why):

```sh
sudo mkdir -p /etc/cloudflared
sudo cp ~/thermal-printer/deploy/cloudflared-config.yml /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml
# fill in: TUNNEL_ID (both places), TEAM_NAME, ACCESS_APP_AUD
```

Run it as a service:

```sh
sudo cloudflared --config /etc/cloudflared/config.yml service install
sudo systemctl start cloudflared
sudo systemctl status cloudflared       # "active (running)"
```

Then the edge header rule. In the Cloudflare dashboard: `cuzeth.com`
zone → **Rules** → **Transform Rules** → **Modify Request Header** →
create rule:

- Name: `strip access identity from friend host`
- When: Custom filter expression → Hostname equals `print.cuzeth.com`
- Then: **Remove** header `Cf-Access-Jwt-Assertion`, **Remove** header
  `Cf-Access-Authenticated-User-Email`

Without this rule, a visitor on the friend hostname could send forged
`Cf-Access-*` headers and Flask would see them; the rule kills them at
the edge (and even then, `auth/access.py` only opens for the
connector-validated `OWNER_EMAIL`).

---

## 9 · Start the app and verify everything

```sh
sudo systemctl start thermal-printer
curl http://localhost:5005/api/ping     # {"ok": true, ...}
```

Now the full drill, mirroring DEPLOY.md step 9:

1. **Console** (any device, any network):
   `https://console.cuzeth.com/` → Access login screen → emailed code →
   full GUI loads. If you get in but everything 403s, `OWNER_EMAIL`
   doesn't match — fix `.env` and restart.
2. **Friends** (phone on cellular):
   - `https://print.cuzeth.com/m/` → friends page with CSS/JS.
   - `https://print.cuzeth.com/` → **404**.
   - `https://print.cuzeth.com/api/ping` → `{"ok": true}`.
3. **Forgery drill** (same phone):
   ```sh
   curl -si -H "Cf-Access-Authenticated-User-Email: you@example.com" https://print.cuzeth.com/ | head -1
   # HTTP/2 404
   ```
4. **Old world is dead**: `https://thermal-printer.<tailnet>.ts.net/`
   fails to connect at all.
5. **End to end**: sign in as a friend on the new domain, print
   something, watch paper come out.

---

## 10 · Retire Tailscale

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

## 11 · Tell the friends

Send everyone the new URL: `https://print.cuzeth.com/m/`. Same
username, same password, same history — they just have to sign in
again, because the old domain's session cookie can't follow them.

Done. You can delete this file once the paper is flowing — the
steady-state runbook is [DEPLOY.md](DEPLOY.md).

---

## If it goes sideways: rollback

The last pre-migration commit is `b344193`. To go back to the old
world at any point:

```sh
cd ~/thermal-printer
git checkout b344193
sudo systemctl restart thermal-printer
sudo systemctl stop cloudflared         # if it got as far as starting
sudo tailscale funnel --bg 5005         # reopen the old door
```

`data/` is untouched by checkout, so friends and history survive the
round trip. When you're ready to retry, `git checkout main` and start
again from step 2.
