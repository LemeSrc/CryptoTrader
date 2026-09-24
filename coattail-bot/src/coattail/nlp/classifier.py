"""Aus einem Beitrag eine Handelsabsicht ableiten.

Zwei Stufen. Die Regelstufe laeuft immer, kostet nichts und faengt die klaren
Faelle ab: ein Kuerzel mit Dollarzeichen, ein Firmenname aus der Zuordnung, ein
Stichwort mit eindeutiger Branchenwirkung. Die Modellstufe kommt nur dazu, wenn
ein Schluessel hinterlegt ist, und beurteilt den Rest.

Die Regelstufe ist bewusst konservativ. Ein Beitrag ohne erkennbaren Bezug
erzeugt kein Signal, statt eines mit halber Ueberzeugung. Im Zweifel nichts zu
tun ist die guenstigere Entscheidung.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..settings import Secrets

log = logging.getLogger(__name__)

TICKER_RE = re.compile(r"\$([A-Z]{1,5})\b")

# Firmenname zu Kuerzel. Bewusst klein gehalten und erweiterbar, weil jede
# falsche Zuordnung direkt in eine Order laufen kann.
COMPANY_MAP: dict[str, str] = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "tesla": "TSLA",
    "amazon": "AMZN",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "meta": "META",
    "lockheed": "LMT",
    "raytheon": "RTX",
    "boeing": "BA",
    "northrop": "NOC",
    "exxon": "XOM",
    "chevron": "CVX",
    "pfizer": "PFE",
    "moderna": "MRNA",
    "intel": "INTC",
    "taiwan semiconductor": "TSM",
    "palantir": "PLTR",
    "coinbase": "COIN",
    "bitcoin": "BTC",
    "ethereum": "ETH",
}

# Thema zu handelbarem Stellvertreter. Wer ueber Zoelle auf Stahl redet, meint
# keinen einzelnen Titel, also nimmt der Bot den Branchenkorb.
THEME_MAP: dict[str, tuple[str, str]] = {
    "tariff": ("XLI", "bearish"),
    "zoll": ("XLI", "bearish"),
    "semiconductor export": ("SOXX", "bearish"),
    "chip export": ("SOXX", "bearish"),
    "defense spending": ("ITA", "bullish"),
    "defense budget": ("ITA", "bullish"),
    "military aid": ("ITA", "bullish"),
    "drilling permit": ("XLE", "bullish"),
    "oil reserve": ("XLE", "bullish"),
    "drug pricing": ("XLV", "bearish"),
    "medicare": ("XLV", "bearish"),
    "crypto reserve": ("BTC", "bullish"),
    "bitcoin reserve": ("BTC", "bullish"),
    "rate cut": ("SPY", "bullish"),
    "interest rate cut": ("SPY", "bullish"),
    "shutdown": ("SPY", "bearish"),
}

BULLISH = {
    "approve", "approved", "boost", "expand", "invest", "support", "deal",
    "agreement", "record", "grant", "award", "cut taxes", "subsidy",
}
BEARISH = {
    "ban", "restrict", "investigate", "sanction", "penalty", "probe", "sue",
    "lawsuit", "halt", "suspend", "tariff", "recall", "fine",
}


@dataclass
class PostSignal:
    symbol: str
    side: str
    conviction: float
    rationale: str
    asset_class: str = "equity"
    horizon_days: int = 21
    detail: dict[str, Any] = field(default_factory=dict)


class RuleClassifier:
    def classify(self, text: str, author: str = "") -> list[PostSignal]:
        lowered = text.lower()
        tone = self._tone(lowered)
        out: list[PostSignal] = []
        seen: set[str] = set()

        for symbol in TICKER_RE.findall(text):
            if symbol in seen:
                continue
            seen.add(symbol)
            out.append(
                PostSignal(
                    symbol=symbol,
                    side="buy" if tone >= 0 else "sell",
                    conviction=0.5 + min(abs(tone), 0.3),
                    rationale=f"Kuerzel im Beitrag von {author}, Tonlage {tone:+.2f}",
                    detail={"matched": "cashtag"},
                )
            )

        for name, symbol in COMPANY_MAP.items():
            if name in lowered and symbol not in seen:
                seen.add(symbol)
                asset = "crypto" if symbol in ("BTC", "ETH") else "equity"
                out.append(
                    PostSignal(
                        symbol=symbol,
                        side="buy" if tone >= 0 else "sell",
                        conviction=0.4 + min(abs(tone), 0.25),
                        rationale=f"Firmenname '{name}' erkannt, Tonlage {tone:+.2f}",
                        asset_class=asset,
                        detail={"matched": "company"},
                    )
                )

        for phrase, (symbol, direction) in THEME_MAP.items():
            if phrase in lowered and symbol not in seen:
                seen.add(symbol)
                out.append(
                    PostSignal(
                        symbol=symbol,
                        side="buy" if direction == "bullish" else "sell",
                        conviction=0.35,
                        rationale=f"Thema '{phrase}' wirkt auf {symbol}",
                        asset_class="crypto" if symbol in ("BTC", "ETH") else "equity",
                        detail={"matched": "theme"},
                    )
                )
        return out

    @staticmethod
    def _tone(lowered: str) -> float:
        pos = sum(1 for w in BULLISH if w in lowered)
        neg = sum(1 for w in BEARISH if w in lowered)
        if pos == neg == 0:
            return 0.0
        return (pos - neg) / max(pos + neg, 1)


SCHEMA = {
    "type": "object",
    "properties": {
        "tradeable": {"type": "boolean"},
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "asset_class": {"type": "string", "enum": ["equity", "crypto", "etf"]},
                    "side": {"type": "string", "enum": ["buy", "sell"]},
                    "conviction": {"type": "number"},
                    "horizon_days": {"type": "integer"},
                    "rationale": {"type": "string"},
                },
                "required": ["symbol", "asset_class", "side", "conviction", "horizon_days", "rationale"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["tradeable", "signals"],
    "additionalProperties": False,
}

SYSTEM = """Du bewertest oeffentliche Aeusserungen von Politikern und Amtstraegern
auf ihre unmittelbare Wirkung an den Maerkten.

Regeln:
- Nur handelbare US-Aktien, breit gehandelte ETFs oder BTC/ETH nennen.
- Nur dann ein Signal ausgeben, wenn der Bezug direkt ist. Allgemeine Politik,
  Wahlkampf oder Personalien sind kein Signal.
- conviction zwischen 0 und 1. Ueber 0.7 nur bei einer konkreten, benannten
  Massnahme mit klarem Betroffenen.
- Bei Unsicherheit tradeable auf false setzen und signals leer lassen.
- rationale in einem kurzen Satz, auf Deutsch."""


class LlmClassifier:
    """Feinauswertung mit der Claude API."""

    def __init__(self, secrets: Secrets, model: str = "claude-sonnet-5") -> None:
        self.model = model
        self.enabled = bool(secrets.anthropic_api_key)
        self._client = None
        if self.enabled:
            try:
                import anthropic

                self._client = anthropic.Anthropic(api_key=secrets.anthropic_api_key)
            except ImportError:
                log.warning("anthropic fehlt: pip install 'coattail[llm]'")
                self.enabled = False

    def classify(self, text: str, author: str = "") -> list[PostSignal]:
        if not self.enabled or self._client is None:
            return []
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=SYSTEM,
                messages=[
                    {
                        "role": "user",
                        "content": f"Beitrag von {author}:\n\n{text[:4000]}",
                    }
                ],
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Modellauswertung fehlgeschlagen: %s", exc)
            return []

        if getattr(response, "stop_reason", None) == "refusal":
            log.info("Modell hat die Auswertung abgelehnt")
            return []
        raw = next((b.text for b in response.content if b.type == "text"), None)
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not data.get("tradeable"):
            return []
        out: list[PostSignal] = []
        for item in data.get("signals", []):
            asset = item.get("asset_class", "equity")
            out.append(
                PostSignal(
                    symbol=str(item["symbol"]).upper(),
                    side=item["side"],
                    conviction=float(item.get("conviction", 0.5)),
                    rationale=item.get("rationale", ""),
                    asset_class="equity" if asset == "etf" else asset,
                    horizon_days=int(item.get("horizon_days", 21)),
                    detail={"matched": "llm", "model": self.model},
                )
            )
        return out


class HybridClassifier:
    """Regeln zuerst, Modell als Ergaenzung. Findet beides dasselbe Kuerzel,
    gewinnt die hoehere Ueberzeugung."""

    def __init__(self, secrets: Secrets, model: str = "claude-sonnet-5", use_llm: bool = True) -> None:
        self.rules = RuleClassifier()
        self.llm = LlmClassifier(secrets, model) if use_llm else None

    def classify(self, text: str, author: str = "") -> list[PostSignal]:
        merged: dict[str, PostSignal] = {}
        for sig in self.rules.classify(text, author):
            merged[sig.symbol] = sig
        if self.llm and self.llm.enabled:
            for sig in self.llm.classify(text, author):
                existing = merged.get(sig.symbol)
                if existing is None or sig.conviction > existing.conviction:
                    merged[sig.symbol] = sig
        return list(merged.values())
