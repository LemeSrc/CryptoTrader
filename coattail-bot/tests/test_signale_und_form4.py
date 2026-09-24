"""Signalfenster und Form-4-Abruf."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from coattail.db import session_scope
from coattail.models import Actor, ActorStat, Signal
from coattail.pipeline.ingest import _store_trade
from coattail.pipeline.signals import build_signals
from coattail.settings import AppConfig, Secrets, SourceConfig
from coattail.sources.base import RawTrade
from coattail.sources.sec import SecForm4Source, ownership_xml


def _cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.sources = [SourceConfig(name="posts_to_signals", kind="none", enabled=False)]
    return cfg


def test_meldung_vor_der_bewertung_geht_nicht_verloren(config):
    """Direkt nach dem Start gibt es noch keine Bewertung. Eine frische
    Meldung muss trotzdem zum Signal werden, sobald die Person freigegeben ist."""
    _store_trade(RawTrade(
        source="house_ptr", external_actor_id="x", actor_name="Hon. Greg Stanton",
        symbol="NVDA", side="buy", transaction_date=dt.date.today() - dt.timedelta(days=4),
        disclosed_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=5),
    ))
    assert build_signals(_cfg(), Secrets()) == 0

    with session_scope() as session:
        actor = session.scalar(select(Actor))
        session.add(ActorStat(actor_id=actor.id, score=70.0, eligible=True, reasons=["ok"]))

    assert build_signals(_cfg(), Secrets()) == 1
    assert build_signals(_cfg(), Secrets()) == 0
    with session_scope() as session:
        assert session.scalar(select(Signal)).symbol == "NVDA"


SUBMISSION = """<SEC-DOCUMENT>0001592900-26-004298.txt : 20260924
<DOCUMENT>
<TYPE>4
<TEXT>
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0001</issuerCik><issuerTradingSymbol>NYAX</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0002</rptOwnerCik>
      <rptOwnerName>NECHMAD YAIR</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-22</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>20000</value></transactionShares>
        <transactionPricePerShare><value>15.5</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
</XML>
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>
"""


def _atom(entries: list[tuple[str, str]]) -> bytes:
    items = "".join(
        f'<entry><link href="{href}"/><updated>{upd}</updated></entry>' for href, upd in entries
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{items}</feed>'.encode()


class _Resp:
    def __init__(self, content: bytes | str, status: int = 200) -> None:
        self.status_code = status
        self.content = content if isinstance(content, bytes) else content.encode()
        self.text = self.content.decode()

    def raise_for_status(self) -> None:
        return None


def test_ownership_xml_aus_vollstaendiger_einreichung():
    root = ownership_xml(SUBMISSION)
    assert root is not None
    assert root.findtext("issuer/issuerTradingSymbol") == "NYAX"
    assert ownership_xml("<XML><other/></XML>") is None


def test_form4_zaehlt_jede_meldung_einmal_und_blaettert(monkeypatch):
    base = "https://www.sec.gov/Archives/edgar/data/{cik}/000159290026004{n:03d}/0001592900-26-004{n:03d}-index.htm"
    now = dt.datetime(2026, 9, 24, 18, 0, tzinfo=dt.UTC)
    page0 = []
    for n in range(50):  # jede Meldung doppelt: Unternehmen und Person
        stamp = (now - dt.timedelta(minutes=n)).isoformat()
        page0.append((base.format(cik=1, n=n), stamp))
        page0.append((base.format(cik=2, n=n), stamp))
    page1 = [(base.format(cik=1, n=200), (now - dt.timedelta(hours=3)).isoformat())]
    pages = {0: page0, 100: page1}
    fetched: list[str] = []

    def fake_get(self, url, params=None, **kwargs):
        if params and params.get("action") == "getcurrent":
            return _Resp(_atom(pages.get(params["start"], [])))
        fetched.append(url)
        return _Resp(SUBMISSION)

    monkeypatch.setattr("coattail.sources.sec.HttpClient.get", fake_get)
    src = SecForm4Source(AppConfig(), Secrets(), SourceConfig(name="sec_form4", kind="sec_form4",
                                                               options={"min_value_usd": 100_000}))
    trades = list(src.fetch(now - dt.timedelta(hours=2)))
    # 50 verschiedene Meldungen, die dritte Stunde liegt vor dem Cursor
    assert len(fetched) == 50
    assert all(u.endswith(".txt") for u in fetched)
    assert len(trades) == 50
    assert trades[0].symbol == "NYAX" and trades[0].side == "buy"
    assert trades[0].actor_type == "insider"
