"""
Kombinierter Einstiegspunkt für Cloud-Hosting.

Startet den Trading-Bot in einem Hintergrund-Thread UND das Web-Dashboard im
Hauptthread -> beides läuft in EINEM Prozess. Das passt zu Free-Tier-Hostern,
die nur einen Prozess pro Service erlauben.

Lokal weiterhin getrennt nutzbar:  python bot.py  /  python dashboard.py
In der Cloud:  python run_all.py   (Startbefehl, siehe Procfile / DEPLOYMENT.md)

Wichtig: nur EINE Instanz starten (kein gunicorn mit mehreren Workern), sonst
würden mehrere Bot-Loops parallel traden.
"""

import logging
import threading

import config
import database as db
import bot
from dashboard import app


def _run_bot():
    try:
        bot.run()
    except Exception:
        logging.exception("Bot-Thread beendet sich mit Fehler")


if __name__ == "__main__":
    db.init_db()
    threading.Thread(target=_run_bot, name="trading-bot", daemon=True).start()
    logging.getLogger("run_all").info(
        "Dashboard startet auf %s:%s (Bot läuft im Hintergrund)",
        config.DASHBOARD_HOST, config.DASHBOARD_PORT,
    )
    # Flask-Entwicklungsserver; für diesen Hobby-Anwendungsfall ausreichend.
    app.run(host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT)
