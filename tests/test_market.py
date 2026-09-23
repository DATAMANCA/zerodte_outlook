import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from zerodte_outlook.sources import market  # noqa: E402


def test_get_overnight_gap_pct_uses_futures_own_change_not_absolute_level(monkeypatch):
    # Regression test for a real bug: ES=F trades at a totally different
    # price scale than SPY (index level vs ETF price, roughly 10x), so
    # comparing the futures' raw price to SPY's close produced nonsense
    # gaps like "+900%". The fix must use the futures' OWN percent change
    # from its own prior settlement, never compared against a different
    # instrument's price level.
    def fake_get_spot(symbol):
        assert symbol == "ES=F"
        return 4620.0

    def fake_get_daily_history(symbol, years=None, period=None):
        assert symbol == "ES=F"
        return pd.DataFrame({"Close": [4600.0]})

    monkeypatch.setattr(market, "get_spot", fake_get_spot)
    monkeypatch.setattr(market, "get_daily_history", fake_get_daily_history)

    gap = market.get_overnight_gap_pct("SPY")
    assert gap == pytest.approx((4620.0 - 4600.0) / 4600.0 * 100)
    assert abs(gap) < 5  # a sane overnight gap, not hundreds of percent


def test_get_daily_history_strips_timezone(monkeypatch):
    # Regression test for a real bug: SPY history comes back tz-aware in
    # America/New_York while ^VIX comes back in America/Chicago, even though
    # both are same-day US session bars - joining them without stripping tz
    # silently produced zero matching rows.
    class FakeTicker:
        def history(self, period, interval, auto_adjust):
            idx = pd.date_range("2026-09-01", periods=3, freq="D", tz="America/Chicago")
            return pd.DataFrame({"Close": [1.0, 2.0, 3.0], "Open": [1.0, 2.0, 3.0]}, index=idx)

    monkeypatch.setattr(market.yf, "Ticker", lambda symbol: FakeTicker())
    hist = market.get_daily_history("^VIX", period="5d")
    assert hist.index.tz is None


def test_num_handles_nan_and_non_numeric():
    assert market._num("3.5") == 3.5
    assert market._num(None) is None
    assert market._num(float("nan")) is None
    assert market._num("not a number") is None
