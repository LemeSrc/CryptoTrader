#!/usr/bin/env bash
# Einrichtung auf einem frischen Debian oder Ubuntu.
# Aufruf: sudo bash deploy/setup_vps.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/coattail}"
APP_USER="${APP_USER:-coattail}"

if [[ $EUID -ne 0 ]]; then
  echo "Bitte mit sudo ausfuehren." >&2
  exit 1
fi

echo "== Pakete =="
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git tzdata logrotate

echo "== Benutzer $APP_USER =="
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"

echo "== Verzeichnisse =="
mkdir -p "$APP_DIR"/{data,logs}
if [[ "$(pwd)" != "$APP_DIR" ]]; then
  cp -r . "$APP_DIR"/
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "== Virtuelle Umgebung =="
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q "$APP_DIR"[brokers,llm]

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "!! $APP_DIR/.env ausfuellen, bevor der Dienst startet."
fi

echo "== Dienst =="
cp "$APP_DIR/deploy/coattail.service" /etc/systemd/system/coattail.service
systemctl daemon-reload

echo "== Logrotation =="
cat > /etc/logrotate.d/coattail <<'ROTATE'
/opt/coattail/logs/*.log {
  weekly
  rotate 8
  compress
  missingok
  notifempty
  copytruncate
}
ROTATE

echo "== Taegliche Sicherung =="
( crontab -l 2>/dev/null | grep -v coattail/deploy/backup.sh ; \
  echo "0 4 * * * $APP_DIR/deploy/backup.sh" ) | crontab -

cat <<INFO

Fertig. Naechste Schritte:

  1. $APP_DIR/.env ausfuellen
  2. $APP_DIR/config/config.yaml pruefen, mode steht auf dry_run
  3. sudo -u $APP_USER $APP_DIR/.venv/bin/coattail doctor
  4. sudo systemctl enable --now coattail
  5. journalctl -u coattail -f

INFO
