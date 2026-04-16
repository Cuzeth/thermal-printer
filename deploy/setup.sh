#!/usr/bin/env bash
# Idempotent Pi setup for thermal-printer.
#
# Run from the repo root:
#   bash deploy/setup.sh
#
# Does:
#   - apt install system deps
#   - create .venv and install requirements
#   - install the udev rule and reload
#   - install the systemd unit (enabled, NOT started)
#
# Does NOT:
#   - start the service (edit .env first, then `sudo systemctl start thermal-printer`)
#   - touch .env

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="${SUDO_USER:-$(id -un)}"
SERVICE_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"

need_sudo() {
  if [[ $EUID -ne 0 ]]; then
    echo "re-running with sudo for: $*"
    sudo "$@"
  else
    "$@"
  fi
}

echo "==> installing system packages"
need_sudo apt-get update
need_sudo apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  libusb-1.0-0-dev libjpeg-dev zlib1g-dev \
  fonts-dejavu fonts-noto-cjk git curl

echo "==> creating virtualenv at $REPO_DIR/.venv"
if [[ ! -d "$REPO_DIR/.venv" ]]; then
  python3 -m venv "$REPO_DIR/.venv"
fi

echo "==> installing Python requirements"
"$REPO_DIR/.venv/bin/pip" install --upgrade pip
"$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt"

echo "==> installing udev rule"
need_sudo install -m 0644 \
  "$REPO_DIR/deploy/99-thermal-printer.rules" \
  /etc/udev/rules.d/99-thermal-printer.rules
need_sudo udevadm control --reload
need_sudo udevadm trigger

echo "==> installing systemd unit for user '$SERVICE_USER'"
UNIT_TMP="$(mktemp)"
sed \
  -e "s|^User=.*|User=$SERVICE_USER|" \
  -e "s|^Group=.*|Group=$SERVICE_USER|" \
  -e "s|^WorkingDirectory=.*|WorkingDirectory=$REPO_DIR|" \
  -e "s|^EnvironmentFile=.*|EnvironmentFile=$REPO_DIR/.env|" \
  -e "s|/home/pi/thermal-printer/\.venv|$REPO_DIR/.venv|g" \
  "$REPO_DIR/deploy/thermal-printer.service" > "$UNIT_TMP"
need_sudo install -m 0644 "$UNIT_TMP" /etc/systemd/system/thermal-printer.service
rm -f "$UNIT_TMP"
need_sudo systemctl daemon-reload
need_sudo systemctl enable thermal-printer.service

mkdir -p "$REPO_DIR/data"

cat <<'DONE'

==> setup complete.

Next steps:
  1. If you haven't already:  cp .env.example .env  and fill in SECRET_KEY + ADMIN_TOKEN.
     Generate them with:
       python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))"
       python3 -c "import secrets; print('ADMIN_TOKEN=' + secrets.token_urlsafe(32))"
  2. Plug the printer in (USB). Unplug/replug if it was already connected
     before the udev rule was installed.
  3. Start the service:
       sudo systemctl start thermal-printer
  4. Watch logs:
       journalctl -u thermal-printer -f
DONE
