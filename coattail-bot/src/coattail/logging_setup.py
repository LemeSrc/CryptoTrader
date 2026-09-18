from __future__ import annotations

import logging
import sys
from pathlib import Path

from rich.logging import RichHandler

_CONFIGURED = False


def setup_logging(level: str = "INFO", logfile: str | None = "logs/coattail.log") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handlers: list[logging.Handler] = [
        RichHandler(rich_tracebacks=True, show_path=False, markup=False)
    ]
    if logfile:
        Path(logfile).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s"))
        handlers.append(fh)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    sys.excepthook = lambda t, v, tb: logging.getLogger("coattail").critical(
        "Unbehandelte Ausnahme", exc_info=(t, v, tb)
    )
    _CONFIGURED = True
