#!/usr/bin/env bash
# Taegliche Sicherung der Datenbank. Der Kurscache ist ersetzbar und bleibt aussen vor.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/coattail}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
DB="$APP_DIR/data/coattail.db"

mkdir -p "$BACKUP_DIR"
[[ -f "$DB" ]] || { echo "Keine Datenbank unter $DB"; exit 0; }

STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$BACKUP_DIR/coattail-$STAMP.db"

# .backup statt cp: sichert konsistent, auch waehrend der Bot schreibt.
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB" ".backup '$OUT'"
else
  cp "$DB" "$OUT"
fi
gzip -f "$OUT"

find "$BACKUP_DIR" -name 'coattail-*.db.gz' -mtime "+$KEEP_DAYS" -delete
echo "Gesichert: $OUT.gz"
