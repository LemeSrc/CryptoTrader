from __future__ import annotations

import numpy as np
import pandas as pd

from study import CLASSES, daily_rules, hourly_rules, net


def kurse(freq: str, n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    idx = pd.date_range("2022-01-03", periods=n, freq=freq)
    return pd.DataFrame({"Open": close * (1 + rng.normal(0, 0.002, n)), "Close": close}, index=idx)


def test_kosten_fallen_bei_einstieg_und_ausstieg_an():
    ret = pd.Series([0.0, 0.10, 0.0, 0.0])
    pos = pd.Series([0.0, 1.0, 1.0, 0.0])
    r = net(pos, ret, cost=0.01)
    assert list(r.round(4)) == [0.0, 0.09, 0.0, -0.01]


def test_keine_regel_schaut_in_die_zukunft():
    """Aendert sich nur der letzte Kurs, darf sich kein frueherer Ertrag aendern."""
    for rules, freq, cfg in (
        (daily_rules, "B", CLASSES["Aktien"]),
        (daily_rules, "D", CLASSES["Krypto"]),
        (hourly_rules, "h", CLASSES["Krypto"]),
    ):
        df = kurse(freq)
        anders = df.copy()
        anders.iloc[-1] *= 3
        vorher, nachher = rules(df, cfg), rules(anders, cfg)
        for name in vorher:
            a, b = vorher[name][0].iloc[:-1], nachher[name][0].iloc[:-1]
            assert np.allclose(a, b), name
