#!/usr/bin/env bash
# Neuen Stand aus GitHub holen und auf dem Server einspielen.
#
# Aufruf aus dem geklonten Repo heraus:
#   sudo bash deploy/update.sh              normales Update
#   sudo bash deploy/update.sh --reset-db   dazu die Datenbank neu anfangen
#   sudo bash deploy/update.sh --keep-config  eigene config.yaml behalten
#
# Die alte Datenbank und die alte Konfiguration werden nie geloescht, nur mit
# Datum umbenannt. Wer zurueck will, benennt sie wieder um.
set -euo pipefail

# Alles steckt in einer Funktion. Bash liest sie komplett ein, bevor sie
# laeuft, deshalb stoert es nicht, wenn git pull dieses Skript selbst ersetzt.
main() {
  APP_DIR="${APP_DIR:-/opt/coattail}"
  APP_USER="${APP_USER:-coattail}"
  SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  STAMP="$(date +%Y%m%d-%H%M%S)"
  RESET_DB=0
  KEEP_CONFIG=0

  for arg in "$@"; do
    case "$arg" in
      --reset-db) RESET_DB=1 ;;
      --keep-config) KEEP_CONFIG=1 ;;
      *) echo "Unbekannte Option: $arg" >&2; exit 1 ;;
    esac
  done

  if [[ $EUID -ne 0 ]]; then
    echo "Bitte mit sudo ausfuehren." >&2
    exit 1
  fi

  if [[ "$SRC_DIR" == "$APP_DIR" ]]; then
    echo "Bitte aus dem geklonten Repo starten, nicht aus $APP_DIR." >&2
    exit 1
  fi

  echo "== Neuen Stand holen =="
  git -C "$SRC_DIR" pull --ff-only

  echo "== Dienst anhalten =="
  systemctl stop coattail 2>/dev/null || true

  if [[ $RESET_DB -eq 1 && -f "$APP_DIR/data/coattail.db" ]]; then
    echo "== Datenbank beiseitelegen =="
    mv "$APP_DIR/data/coattail.db" "$APP_DIR/data/coattail.db.bak-$STAMP"
    rm -f "$APP_DIR/data/coattail.db-wal" "$APP_DIR/data/coattail.db-shm"
    echo "   alte Datenbank: $APP_DIR/data/coattail.db.bak-$STAMP"
  fi

  if [[ -f "$APP_DIR/config/config.yaml" ]]; then
    if [[ $KEEP_CONFIG -eq 1 ]]; then
      cp "$APP_DIR/config/config.yaml" "/tmp/coattail-config-$STAMP.yaml"
    else
      cp "$APP_DIR/config/config.yaml" "$APP_DIR/config/config.yaml.bak-$STAMP"
      echo "   alte Konfiguration: $APP_DIR/config/config.yaml.bak-$STAMP"
    fi
  fi

  echo "== Dateien kopieren =="
  # .env, Datenbank, Logs und die virtuelle Umgebung bleiben unangetastet.
  tar -C "$SRC_DIR" \
    --exclude=./.venv --exclude=./data --exclude=./logs --exclude=./.env \
    --exclude=./build --exclude='*.egg-info' --exclude='__pycache__' \
    -cf - . | tar -C "$APP_DIR" -xf -
  if [[ $KEEP_CONFIG -eq 1 && -f "/tmp/coattail-config-$STAMP.yaml" ]]; then
    mv "/tmp/coattail-config-$STAMP.yaml" "$APP_DIR/config/config.yaml"
    echo "   eigene config.yaml behalten, neue Vorlage liegt im Repo unter config/config.yaml"
  fi
  mkdir -p "$APP_DIR"/{data,logs}
  chown -R "$APP_USER:$APP_USER" "$APP_DIR"

  echo "== Programm neu installieren =="
  sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q "$APP_DIR"[brokers,llm]
  # Ohne force-reinstall bleibt bei gleicher Versionsnummer der alte Code liegen.
  sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q --force-reinstall --no-deps "$APP_DIR"

  echo "== Dienstdatei =="
  cp "$APP_DIR/deploy/coattail.service" /etc/systemd/system/coattail.service
  systemctl daemon-reload

  echo "== Pruefung =="
  cd "$APP_DIR"
  sudo -u "$APP_USER" "$APP_DIR/.venv/bin/coattail" doctor || true

  cat <<INFO

Update eingespielt, der Dienst ist noch aus.

Bei frischer Datenbank zuerst die Historie laden (dauert einige Minuten):
  cd $APP_DIR && sudo -u $APP_USER .venv/bin/coattail bootstrap

Dann starten:
  sudo systemctl start coattail
  journalctl -u coattail -f

INFO
}

main "$@"
