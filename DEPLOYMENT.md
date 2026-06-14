# 24/7-Hosting des Bots (kostenfrei / günstig)

Ziel: Bot + Dashboard laufen rund um die Uhr, **ohne** deinen PC, und du kannst
schnell Änderungen vornehmen – idealerweise mit KI-Unterstützung.

Der kombinierte Start erfolgt über **[`run_all.py`](run_all.py)** (Bot-Loop im
Hintergrund-Thread + Dashboard in einem Prozess). Lokal weiterhin getrennt
nutzbar (`python bot.py` / `python dashboard.py`).

---

## Ehrlicher Realitäts-Check

Eine Plattform, die **gleichzeitig** „komplett gratis", „24/7 ohne Schlafmodus"
**und** „KI direkt eingebaut" ist, gibt es nicht sauber. Die sinnvollen
Kompromisse:

| Plattform | 24/7 gratis? | Schnelle Änderungen | Aufwand | Empfehlung für … |
|---|---|---|---|---|
| **Railway** | Trial-Guthaben, danach ~3–5 $/Monat | ⭐ Top: Push zu GitHub → Auto-Deploy | gering | **KI-Workflow mit mir** |
| **Replit** | Always-On nur im Bezahlplan (~20 $/Mo) | ⭐ KI-Agent direkt eingebaut | sehr gering | KI **in** der Plattform |
| **Fly.io** | Free-Allowance (Kreditkarte nötig) | ok (CLI/Git) | mittel | technisch versiert |
| **Oracle Cloud Free** | ✅ wirklich dauerhaft gratis | ⛔ manuell (SSH/systemd) | hoch | maximale Sparsamkeit |
| **Render (Free Web)** | ⛔ schläft nach 15 Min Inaktivität | ok | gering | ungeeignet für Bots |

**Meine Empfehlung für „schnell ändern, evtl. mit KI":**
**GitHub-Repo + Railway.** Ich (Claude) bearbeite den Code, du pushst zu GitHub,
Railway deployt automatisch neu. Env-Variablen änderst du per Klick in der UI –
ganz ohne Deploy (siehe unten). Kosten nach dem Gratis-Guthaben sehr niedrig.

Wer es **100 % gratis** braucht und einmal Setup-Aufwand akzeptiert:
**Oracle Cloud Always Free** (eine kleine ARM-VM, Bot als `systemd`-Dienst).

---

## Weg A — Railway (empfohlen)

1. **GitHub-Repo anlegen** und dieses Projekt hochladen (`data/` ist via
   `.gitignore` ausgenommen – die DB gehört nicht ins Repo).
2. Auf [railway.app](https://railway.app) mit GitHub einloggen →
   **New Project → Deploy from GitHub repo** → dieses Repo wählen.
3. Railway erkennt Python automatisch und nutzt das **`Procfile`**
   (`web: python run_all.py`).
4. **Variables** (Env Vars) setzen:
   - `CT_HOST = 0.0.0.0`  (damit das Dashboard erreichbar ist)
   - optional: `CT_START_CAPITAL`, `CT_SCAN_INTERVAL_SECONDS`,
     `CT_ENTRY_SCORE_THRESHOLD`, `CT_TOP_N_SYMBOLS`, `CT_ALLOW_SHORT` …
   - `PORT` wird von Railway automatisch gesetzt.
5. **Persistente Datenbank:** unter *Volumes* ein Volume anlegen (z. B. Mount
   `/data`) und `CT_DB_PATH = /data/trades.db` setzen. Sonst sind die Trades
   nach jedem Deploy weg.
6. Deploy abwarten → unter *Settings → Networking* eine öffentliche Domain
   erzeugen → das ist die URL deines Dashboards.

**Änderungen danach:** Ich passe den Code an → du `git push` → Railway deployt
automatisch. Reine Parameter (Kapital, Schwelle, …) gehen sogar ohne Deploy über
die Env-Variablen.

---

## Weg B — Oracle Cloud Always Free  ⭐ (gewählt – dauerhaft gratis)

Einmaliger Aufwand ~20–30 Min. Alles Nötige liegt im Ordner [`deploy/`](deploy/).

### 1. VM erstellen
- Oracle-Cloud-Konto anlegen (Always-Free, Kreditkarte nur zur Verifikation,
  keine Kosten).
- **Compute → Instances → Create**: Image **Ubuntu 22.04/24.04**, Shape
  **VM.Standard.A1.Flex** (ARM, Always-Free; z. B. 1 OCPU / 6 GB). SSH-Key
  hinterlegen und die **Public IP** notieren.

### 2. Code auf die VM bringen
Per SSH verbinden (`ssh ubuntu@<PUBLIC_IP>`), dann das Repo klonen:
```bash
sudo apt-get update && sudo apt-get install -y git
git clone <DEIN_GITHUB_REPO> CryptoTrader
cd CryptoTrader
```
> Kein GitHub? Alternativ die Dateien per `scp` hochladen. Ein GitHub-Repo
> macht spätere Updates (ich ändere Code → `git pull` auf der VM) aber viel
> einfacher.

### 3. Einrichten (ein Befehl)
```bash
bash deploy/setup_oracle.sh
```
Das Skript legt ein `venv` an, installiert die Abhängigkeiten und richtet den
**systemd-Dienst** `cryptotrader` ein (startet automatisch, auch nach Reboot).

### 4. Dashboard absichern (Pflicht, da öffentlich!)
In `/etc/systemd/system/cryptotrader.service` die Zeile einkommentieren/anpassen:
```
Environment=CT_DASHBOARD_PASSWORD=DeinSicheresPasswort
```
dann neu laden:
```bash
sudo systemctl daemon-reload && sudo systemctl restart cryptotrader
```
Ohne Passwort wäre der **Reset-Button** für jeden im Internet erreichbar.

### 5. Port 5000 freigeben (zwei Ebenen!)
- **Cloud:** in der Oracle-Konsole beim VCN → *Security List* (oder NSG) eine
  **Ingress-Regel** für TCP **5000**, Quelle `0.0.0.0/0` hinzufügen.
- **OS:** erledigt das Setup-Skript bereits (iptables + `netfilter-persistent`).

Dashboard danach: `http://<PUBLIC_IP>:5000` (Login: `admin` + dein Passwort).

### Betrieb
```bash
sudo systemctl status cryptotrader --no-pager   # läuft es?
journalctl -u cryptotrader -f                    # Live-Logs
git pull && sudo systemctl restart cryptotrader  # Update einspielen
```

> **Noch sicherer** statt Port 5000 öffnen: Port zu lassen und das Dashboard per
> SSH-Tunnel ansehen: `ssh -L 5000:localhost:5000 ubuntu@<PUBLIC_IP>` →
> dann lokal `http://localhost:5000`.

---

## Wichtige Hinweise

- **Binance-Geoblocking:** Manche Cloud-Regionen blockt Binance (HTTP 451). Der
  Client weicht automatisch auf `data-api.binance.vision` aus. Falls dennoch
  blockiert: andere Region wählen (z. B. EU statt US).
- **Nur EINE Instanz / ein Worker** (kein gunicorn mit mehreren Workern) – sonst
  laufen mehrere Bot-Loops parallel und traden doppelt.
- **Kein echtes Geld, keine API-Keys** – es bleibt Paper-Trading. Es gibt daher
  keine Secrets, die geschützt werden müssten.
- **Schnelle Parameter-Änderungen** ohne Code: über `CT_*`-Env-Variablen
  (siehe [`config.py`](config.py), Abschnitt „Laufzeit-Overrides").
