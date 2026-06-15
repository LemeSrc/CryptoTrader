#!/usr/bin/env bash
#
# Einmal-Setup auf einer Oracle-Cloud-Always-Free-VM (Ubuntu, ARM oder x86).
# Installiert Abhaengigkeiten in einem venv und richtet den systemd-Dienst ein,
# der Bot + Dashboard via run_all.py dauerhaft laufen laesst.
#
# Aufruf (im geklonten Repo):   bash deploy/setup_oracle.sh
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="$(whoami)"
echo ">> App-Verzeichnis: $APP_DIR (User: $RUN_USER)"

# --- Swap anlegen: wichtig auf der 1-GB-VM (E2.1.Micro) gegen Out-of-Memory ---
TOTAL_MB="$(free -m | awk '/^Mem:/{print $2}')"
if [ ! -f /swapfile ] && [ "${TOTAL_MB:-9999}" -lt 1500 ]; then
    echo ">> Wenig RAM (${TOTAL_MB} MB) erkannt -> 2 GB Swap anlegen ..."
    sudo fallocate -l 2G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo ">> Pakete installieren ..."
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip

echo ">> venv anlegen und Abhaengigkeiten installieren ..."
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo ">> systemd-Dienst installieren ..."
sed "s#__APP_DIR__#${APP_DIR}#g; s#__USER__#${RUN_USER}#g" \
    deploy/cryptotrader.service | sudo tee /etc/systemd/system/cryptotrader.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now cryptotrader

echo ">> Firewall (OS) fuer Port 5000 oeffnen ..."
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 5000 -j ACCEPT || true
if command -v netfilter-persistent >/dev/null 2>&1; then
    sudo netfilter-persistent save || true
fi

echo ""
echo "FERTIG. Status:   sudo systemctl status cryptotrader --no-pager"
echo "Logs:             journalctl -u cryptotrader -f"
echo ""
echo "NOCH ZU TUN:"
echo "  1) In der Oracle-Konsole eine Ingress-Regel fuer TCP 5000 in der"
echo "     Security List / NSG des VCN hinzufuegen (Quelle 0.0.0.0/0)."
echo "  2) Dashboard-Passwort setzen: in /etc/systemd/system/cryptotrader.service"
echo "     die Zeile CT_DASHBOARD_PASSWORD einkommentieren, dann:"
echo "        sudo systemctl daemon-reload && sudo systemctl restart cryptotrader"
echo "  3) Dashboard: http://<DEINE_PUBLIC_IP>:5000"
