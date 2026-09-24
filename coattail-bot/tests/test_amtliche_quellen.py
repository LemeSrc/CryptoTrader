"""Amtliche Kongressmeldungen ueber den Tagesspiegel. Die Fixtures haben
exakt die Spalten der echten Dateien, damit ein Formatwechsel hier auffaellt."""

from __future__ import annotations

import datetime as dt

from coattail.settings import AppConfig, Secrets, SourceConfig
from coattail.sources.congress import HousePtrSource, SenatePtrSource

HOUSE_INDEX = """﻿<?xml version="1.0" encoding="utf-8"?>
<FinancialDisclosure>
  <Member><Prefix>Hon.</Prefix><Last>Stanton</Last><First>Greg</First><Suffix />
    <FilingType>P</FilingType><StateDst>AZ04</StateDst><Year>2026</Year>
    <FilingDate>9/10/2026</FilingDate><DocID>20030001</DocID></Member>
  <Member><Prefix /><Last>Aaron</Last><First>Richard</First><Suffix />
    <FilingType>W</FilingType><StateDst>MI04</StateDst><Year>2026</Year>
    <FilingDate>4/15/2026</FilingDate><DocID>8068</DocID></Member>
</FinancialDisclosure>
"""

HOUSE_HEADER = (
    "filing_id,politician,member_status,state_district,source_year,transaction_number_in_filing,"
    "owner,asset_v8_2_cleaned,ticker_v8_2_cleaned,asset_type,transaction_type,transaction_date,"
    "notification_date,amount_category,amount_min,amount_max,filing_status,review_level,"
    "needs_review,original_pdf_url"
)
HOUSE_ROWS = [
    # gueltiger Kauf
    '20030001,Hon. Greg Stanton,Member,AZ04,2026,1,SP,NVIDIA Corporation,NVDA,ST,P,2026-08-20,'
    '2026-08-21,"$1,001 - $15,000",1001.0,15000.0,New,,False,https://x/20030001.pdf',
    # Teilverkauf
    '20030001,Hon. Greg Stanton,Member,AZ04,2026,2,SP,Apple Inc.,AAPL,ST,S (partial),2026-08-22,'
    '2026-08-23,"$15,001 - $50,000",15001.0,50000.0,New,,False,https://x/20030001.pdf',
    # zur Pruefung markiert, fehlgelesenes Jahr
    '20030001,Hon. Greg Stanton,Member,AZ04,2026,3,SP,IBM,IBM,ST,P,3026-08-20,'
    '2026-08-21,"$1,001 - $15,000",1001.0,15000.0,New,high,True,https://x/20030001.pdf',
    # Staatsanleihe, kein Aktienhandel
    '20030001,Hon. Greg Stanton,Member,AZ04,2026,4,SP,US Treasury,,GS,P,2026-08-20,'
    '2026-08-21,"$1,001 - $15,000",1001.0,15000.0,New,,False,https://x/20030001.pdf',
    # Tausch
    '20030001,Hon. Greg Stanton,Member,AZ04,2026,5,SP,Old Co,OLD,ST,E,2026-08-20,'
    '2026-08-21,"$1,001 - $15,000",1001.0,15000.0,New,,False,https://x/20030001.pdf',
    # Meldung nicht im Index
    '99999999,Hon. Someone Else,Member,TX01,2026,1,,Tesla,TSLA,ST,P,2026-08-20,'
    '2026-08-21,"$1,001 - $15,000",1001.0,15000.0,New,,False,https://x/99999999.pdf',
    # geloeschte Meldung
    '20030001,Hon. Greg Stanton,Member,AZ04,2026,6,SP,Microsoft,MSFT,ST,P,2026-08-20,'
    '2026-08-21,"$1,001 - $15,000",1001.0,15000.0,Deleted,,False,https://x/20030001.pdf',
]

SENATE_HEADER = (
    "transaction_id,chamber,first_name,last_name,filer_name,office,filing_date,report_title,"
    "report_id,report_key,report_url,item_number,transaction_date,owner,ticker,asset_name,"
    "asset_type,transaction_type,amount,amount_min,amount_max,comment,source_format,"
    "extraction_method,verification_status,scraped_at_utc"
)
SENATE_ROWS = [
    'a1,Senate,John,Boozman,John Boozman,"Boozman, John (Senator)",2026-08-24,PTR,r1,r1,'
    'https://efd/r1,1,2025-11-21,Joint,VEA,Vanguard ETF,Stock,Sale (Partial),'
    '"$1,001 - $15,000",1001,15000,,electronic_html,official_html_table,source_structured,x',
    'a2,Senate,Thomas,Tuberville,Thomas Tuberville,"Tuberville, Tommy (Senator)",2026-09-01,PTR,r2,r2,'
    'https://efd/r2,1,2026-08-15,Self,LMT,Lockheed Martin,Stock,Purchase,'
    '"$15,001 - $50,000",15001,50000,,electronic_html,official_html_table,source_structured,x',
    'a3,Senate,Thomas,Tuberville,Thomas Tuberville,"Tuberville, Tommy (Senator)",2026-09-01,PTR,r2,r2,'
    'https://efd/r2,2,2026-08-15,Self,XOM,Exxon,Stock,Exchange,'
    '"$15,001 - $50,000",15001,50000,,electronic_html,official_html_table,source_structured,x',
    'a4,Senate,Thomas,Tuberville,Thomas Tuberville,"Tuberville, Tommy (Senator)",2026-09-01,PTR,r2,r2,'
    'https://efd/r2,3,2026-08-15,Self,--,Some Muni,Municipal Security,Purchase,'
    '"$15,001 - $50,000",15001,50000,,electronic_html,official_html_table,source_structured,x',
]


def _src(cls):
    return cls(AppConfig(), Secrets(), SourceConfig(name=cls.name, kind=cls.name))


def test_house_index_nur_transaktionsmeldungen():
    filed = HousePtrSource.parse_index(HOUSE_INDEX)
    assert filed == {"20030001": dt.date(2026, 9, 10)}


def test_house_zeilen_werden_sauber_gefiltert():
    src = _src(HousePtrSource)
    text = "\n".join([HOUSE_HEADER, *HOUSE_ROWS])
    trades = list(src.parse_csv(text, {"20030001": dt.date(2026, 9, 10)}, dt.date(2026, 1, 1)))
    assert [(t.symbol, t.side) for t in trades] == [("NVDA", "buy"), ("AAPL", "sell")]
    first = trades[0]
    assert first.actor_name == "Hon. Greg Stanton"
    assert first.disclosed_at == dt.datetime(2026, 9, 10, tzinfo=dt.UTC)
    assert first.transaction_date == dt.date(2026, 8, 20)
    assert (first.amount_low, first.amount_high) == (1001.0, 15000.0)
    assert first.chamber == "house" and first.state == "AZ"


def test_house_fenster_schneidet_alte_meldungen_ab():
    src = _src(HousePtrSource)
    text = "\n".join([HOUSE_HEADER, *HOUSE_ROWS])
    assert list(src.parse_csv(text, {"20030001": dt.date(2026, 9, 10)}, dt.date(2026, 9, 11))) == []


def test_senat_zeilen():
    src = _src(SenatePtrSource)
    text = "\n".join([SENATE_HEADER, *SENATE_ROWS])
    trades = list(src.parse_csv(text, dt.date(2026, 1, 1)))
    assert [(t.actor_name, t.symbol, t.side) for t in trades] == [
        ("John Boozman", "VEA", "sell"),
        ("Thomas Tuberville", "LMT", "buy"),
    ]
    assert trades[1].disclosed_at == dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    assert trades[1].chamber == "senate"


def test_spiegel_schaut_hinter_den_cursor():
    src = _src(SenatePtrSource)
    since = dt.datetime(2026, 9, 20, tzinfo=dt.UTC)
    assert src._window_start(since) == dt.date(2026, 9, 6)
    first = src._window_start(None)
    assert (dt.datetime.now(dt.UTC).date() - first).days == AppConfig().scoring.lookback_days


def test_spiegel_nutzt_etag(monkeypatch):
    src = _src(SenatePtrSource)
    calls: list[dict] = []

    class Resp:
        def __init__(self, status, text="", etag=None):
            self.status_code = status
            self.text = text
            self.headers = {"ETag": etag} if etag else {}

        def raise_for_status(self):
            return None

    responses = [Resp(200, "inhalt", etag='"abc"'), Resp(304)]

    def fake_get(self, url, **kwargs):
        calls.append(kwargs.get("headers") or {})
        return responses.pop(0)

    monkeypatch.setattr("coattail.sources.congress.HttpClient.get", fake_get)
    from coattail.http import HttpClient

    with HttpClient() as client:
        assert src._get_text(client, "https://x/a.csv") == "inhalt"
        assert src._get_text(client, "https://x/a.csv") == "inhalt"
    assert calls[1] == {"If-None-Match": '"abc"'}


def test_federal_register_fragt_gueltige_felder_ab(monkeypatch):
    from coattail.sources.policy import FederalRegisterSource

    seen: dict = {}
    today = dt.datetime.now(dt.UTC).date().isoformat()

    def fake_json(self, url, params=None, **kwargs):
        seen.update(params or {})
        return {"results": [{
            "title": "Adjusting Imports of Steel", "abstract": "tariff", "document_number": "2026-1",
            "publication_date": today, "html_url": "https://fr/1", "subtype": "Proclamation",
            "type": "Presidential Document", "agencies": [{"name": "Executive Office"}],
        }]}

    monkeypatch.setattr("coattail.sources.policy.HttpClient.json", fake_json)
    src = FederalRegisterSource(
        AppConfig(), Secrets(),
        SourceConfig(name="federal_register", kind="federal_register", options={"types": ["PRESDOCU"]}),
    )
    posts = list(src.fetch(None))
    assert "presidential_document_type" not in seen["fields[]"]
    assert posts[0].author == "Proclamation"
    # heutiges Dokument bekommt den Abrufzeitpunkt, nicht Mitternacht
    assert dt.datetime.now(dt.UTC) - posts[0].posted_at < dt.timedelta(minutes=1)
