"""Parser-Tests ohne Netz. Die Zeilen entsprechen dem Format der jeweiligen
Quelle, damit ein Formatwechsel hier auffaellt und nicht erst im Handel."""

from __future__ import annotations

from coattail.settings import AppConfig, Secrets, SourceConfig
from coattail.sources.base import normalize_side
from coattail.sources.congress import CapitolTradesSource, StockWatcherSource
from coattail.sources.traders import HyperliquidSource
from coattail.util import clean_symbol, parse_amount_range


def _src(cls, **options):
    return cls(AppConfig(), Secrets(), SourceConfig(name=cls.name, kind=cls.name, options=options))


def test_seitenerkennung_ueber_quellen_hinweg():
    assert normalize_side("Purchase") == "buy"
    assert normalize_side("purchase") == "buy"
    assert normalize_side("Sale (Full)") == "sell"
    assert normalize_side("sale_partial") == "sell"
    assert normalize_side("exchange") == "sell"
    assert normalize_side("receive") is None


def test_stockwatcher_zeile():
    src = _src(StockWatcherSource)
    rows = [
        {
            "representative": "Nancy Pelosi",
            "ticker": "NVDA",
            "type": "purchase",
            "transaction_date": "2026-01-15",
            "disclosure_date": "2026-02-10",
            "amount": "$1,000,001 - $5,000,000",
            "state": "CA",
            "asset_description": "NVIDIA Corporation Call Options",
        },
        {"representative": "", "ticker": "--", "type": "purchase"},  # unvollstaendig
    ]
    out = list(src._parse(rows, "house", None))
    assert len(out) == 1
    trade = out[0]
    assert trade.symbol == "NVDA"
    assert trade.side == "buy"
    assert trade.amount_low == 1_000_001
    assert trade.amount_high == 5_000_000
    assert trade.option_type == "option"
    assert trade.chamber == "house"
    assert (trade.disclosed_at.date() - trade.transaction_date).days == 26


def test_capitoltrades_zeile():
    src = _src(CapitolTradesSource)
    row = {
        "politician": {
            "fullName": "Tommy Tuberville",
            "_politicianId": "T000278",
            "party": "republican",
            "chamber": "senate",
        },
        "asset": {"assetTicker": "LMT:US", "assetType": "stock"},
        "txType": "buy",
        "txDate": "2026-03-02",
        "pubDate": "2026-03-20T10:00:00Z",
        "value": 45000,
    }
    trade = src._parse_row(row)
    assert trade is not None
    assert trade.symbol == "LMT"
    assert trade.side == "buy"
    assert trade.actor_name == "Tommy Tuberville"
    assert trade.asset_class == "equity"


def test_hyperliquid_richtungen():
    src = _src(HyperliquidSource, wallets=["0xabc"])
    assert src._side("Open Long") == "buy"
    assert src._side("Close Long") == "sell"
    assert src._side("Open Short") == "sell"
    assert src._side("Close Short") == "buy"
    assert src._side(None) is None

    fill = {
        "coin": "ETH", "dir": "Open Long", "time": 1770000000000,
        "px": "3200.5", "sz": "1.5", "closedPnl": "0",
    }
    trade = src._parse_fill(fill, "0xabc", "Wallet A")
    assert trade.symbol == "ETH"
    assert trade.side == "buy"
    assert trade.price == 3200.5
    assert trade.quantity == 1.5
    assert trade.asset_class == "crypto"


def test_wallet_konfiguration_beide_schreibweisen():
    a = _src(HyperliquidSource, wallets=["0x1", "0x2"])._wallets()
    b = _src(HyperliquidSource, wallets=[{"address": "0x1", "label": "Alpha"}])._wallets()
    assert set(a) == {"0x1", "0x2"}
    assert b == {"0x1": "Alpha"}


def test_symbol_saeuberung():
    assert clean_symbol(" nvda ") == "NVDA"
    assert clean_symbol("BRK.B") == "BRK.B"
    assert clean_symbol("--") is None
    assert clean_symbol("") is None
    assert clean_symbol("LMT:US") == "LMT"          # Boersenkuerzel abgeschnitten
    assert clean_symbol("AAPL - Apple Inc") == "AAPL"  # Firmenname abgeschnitten
    assert clean_symbol("UNGEWOEHNLICHLANG") is None   # zu lang fuer einen Ticker


def test_betragsspanne():
    assert parse_amount_range("$1,001 - $15,000") == (1001.0, 15000.0)
    assert parse_amount_range("$50,000") == (50000.0, 50000.0)
    assert parse_amount_range(None) == (None, None)
