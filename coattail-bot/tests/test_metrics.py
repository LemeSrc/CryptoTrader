from __future__ import annotations

import datetime as dt

from coattail.scoring import metrics


def test_shrinkage_bestraft_kleine_stichproben():
    """Sieben von zehn ist keine Trefferquote von 70 Prozent."""
    klein = metrics.shrunk_rate(7, 10)
    gross = metrics.shrunk_rate(70, 100)
    assert klein < gross < 0.7
    assert 0.5 < klein < 0.6


def test_shrinkage_ohne_daten_ist_muenzwurf():
    assert metrics.shrunk_rate(0, 0, prior=0.5) == 0.5


def test_wilson_ist_pessimistischer_als_der_rohwert():
    assert metrics.wilson_lower_bound(7, 10) < 0.7
    assert metrics.wilson_lower_bound(700, 1000) > metrics.wilson_lower_bound(7, 10)


def test_profit_factor_gedeckelt_ohne_verluste():
    assert metrics.profit_factor([0.1, 0.2, 0.3]) == 3.0
    assert metrics.profit_factor([]) == 0.0


def test_max_drawdown():
    dd = metrics.max_drawdown([0.5, -0.5, -0.5, 0.2])
    assert 0.6 < dd < 0.9


def test_r_multiple_rechnet_in_risiko_einheiten():
    # Einstieg 100, Stop 90, Ausstieg 130 sind drei Einheiten Risiko
    assert metrics.r_multiple(100, 130, 90, "buy") == 3.0
    assert metrics.r_multiple(100, 95, 90, "buy") == -0.5
    assert metrics.r_multiple(100, 130, None, "buy") is None


def test_freshness_halbiert_sich_nach_der_halbwertszeit():
    heute = metrics.freshness(dt.date.today(), 365)
    vor_einem_jahr = metrics.freshness(dt.date.today() - dt.timedelta(days=365), 365)
    assert heute > 0.99
    assert abs(vor_einem_jahr - 0.5) < 0.01
    assert metrics.freshness(None) == 0.0


def test_consistency_zaehlt_positive_zeitraeume():
    assert metrics.consistency({"2024": [0.1], "2025": [-0.1], "2026": [0.2]}) == 2 / 3
    assert metrics.consistency({}) == 0.0
