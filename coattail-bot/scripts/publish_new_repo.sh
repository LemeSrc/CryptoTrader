#!/usr/bin/env bash
# Legt dieses Verzeichnis als eigenstaendiges Repository an und schiebt es hoch.
#
# Hintergrund: die GitHub-App dieser Sitzung darf keine Repositories anlegen.
# Das Projekt liegt deshalb als Unterordner im CryptoTrader-Repo und wird mit
# diesem Skript in ein eigenes ueberfuehrt, mit sauberer Historie.
#
# Aufruf:
#   1. Auf https://github.com/new ein leeres Repo "coattail-bot" anlegen
#      (ohne README, ohne .gitignore, ohne Lizenz)
#   2. bash scripts/publish_new_repo.sh <dein-github-name> [repo-name]
set -euo pipefail

OWNER="${1:?Aufruf: publish_new_repo.sh <github-name> [repo-name]}"
REPO="${2:-coattail-bot}"
BRANCH="${3:-main}"

cd "$(dirname "$0")/.."

if [[ -d .git ]]; then
  echo "Hier liegt schon ein .git-Verzeichnis. Abbruch, damit nichts ueberschrieben wird."
  exit 1
fi

git init -b "$BRANCH"
git add .
git commit -m "Coattail: Copy-Trading nach oeffentlichen Spuren

Bewertet Politiker, Insider und Trader aus ihrer eigenen Historie und
kopiert nur, wer die Pruefung besteht. Trockenlauf als Voreinstellung."

git remote add origin "https://github.com/$OWNER/$REPO.git"

for versuch in 1 2 3 4; do
  if git push -u origin "$BRANCH"; then
    echo "Fertig: https://github.com/$OWNER/$REPO"
    exit 0
  fi
  wartezeit=$((2 ** versuch))
  echo "Push fehlgeschlagen, neuer Versuch in ${wartezeit}s ..."
  sleep "$wartezeit"
done

echo "Push nach vier Versuchen fehlgeschlagen. Repo angelegt? Rechte vorhanden?"
exit 1
