import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from zerodte_outlook import features  # noqa: E402


def _flat_history(n=40, price=100.0):
    return pd.DataFrame({"Close": [price] * n})


def _trending_history(n=40, start=90.0, end=110.0):
    step = (end - start) / (n - 1)
    return pd.DataFrame({"Close": [start + i * step for i in range(n)]})


# --------------------------------------------------------------------- gap/RSI


def test_technical_signal_day_gap_dominates_and_clips():
    # 0.85 gap-weight + 0.15 RSI-weight means an extreme gap alone tops out
    # at 0.85, not 1.0 (only reaches 1.0 if RSI also agrees) - the point of
    # this test is that it stays bounded to [-1, 1], not that it hits the
    # exact edge.
    history = _flat_history()
    value, _ = features.technical_signal_day(history, gap_pct=50.0)
    assert 0.85 <= value <= 1.0
    value, _ = features.technical_signal_day(history, gap_pct=-50.0)
    assert -1.0 <= value <= -0.85


def test_technical_signal_day_falls_back_without_enough_history_for_vol():
    history = pd.DataFrame({"Close": [100.0, 100.5]})  # too short for vol/RSI
    value, detail = features.technical_signal_day(history, gap_pct=1.0)
    assert detail["realized_vol_pct"] is None
    assert -1.0 <= value <= 1.0


def test_technical_signal_day_zero_gap_is_neutral_ish():
    history = _flat_history()
    value, _ = features.technical_signal_day(history, gap_pct=0.0)
    assert abs(value) < 0.2  # only the small RSI nudge can move it off 0


# --------------------------------------------------------------------- week trend


def test_technical_signal_week_uptrend_is_positive():
    value, detail = features.technical_signal_week(_trending_history())
    assert value > 0
    assert detail["sma_short"] > detail["sma_long"]


def test_technical_signal_week_downtrend_is_negative():
    value, _ = features.technical_signal_week(_trending_history(start=110.0, end=90.0))
    assert value < 0


def test_technical_signal_week_insufficient_history_returns_none():
    value, detail = features.technical_signal_week(_flat_history(n=5))
    assert value is None
    assert "reason" in detail


# --------------------------------------------------------------------- IV regime


def test_iv_regime_signal_elevated_vix_leans_bearish():
    vix_history = pd.DataFrame({"Close": [15.0] * 20})
    vix_term = {"vix": 20.0, "vix9d": 19.0, "ratio": 0.95}  # well above its own avg
    value, detail = features.iv_regime_signal(vix_history, vix_term)
    assert value < 0


def test_iv_regime_signal_term_structure_inversion_leans_bearish():
    vix_history = pd.DataFrame({"Close": [15.0] * 20})
    vix_term = {"vix": 15.0, "vix9d": 18.0, "ratio": 1.2}  # inverted: near-term fear spike
    value, _ = features.iv_regime_signal(vix_history, vix_term)
    assert value < 0


# --------------------------------------------------------------------- options flow


def test_options_flow_signal_uncalibrated_without_history():
    value, detail = features.options_flow_signal(
        {"put_call_oi_ratio": 1.5, "spot_vs_flip_pct": 2.0}, pd.DataFrame(columns=["put_call_oi_ratio"]))
    assert value == 0.0
    assert detail["calibrated"] is False


def test_options_flow_signal_put_heavy_book_is_bullish_contrarian():
    # Regression test for a real sign-inversion bug: a put-heavy reading
    # relative to our own logged history must lean BULLISH (contrarian), not
    # bearish - this was inverted until caught by this test suite.
    history = pd.DataFrame({"put_call_oi_ratio": [0.9, 1.0, 1.1] * 5})
    chain_summary = {"put_call_oi_ratio": 3.0, "spot_vs_flip_pct": None}  # way above history
    value, detail = features.options_flow_signal(chain_summary, history)
    assert detail["calibrated"] is True
    assert detail["put_call_oi_zscore"] > 0
    assert value > 0  # put-heavy -> bullish, not bearish


def test_options_flow_signal_call_heavy_book_is_bearish_contrarian():
    history = pd.DataFrame({"put_call_oi_ratio": [0.9, 1.0, 1.1] * 5})
    chain_summary = {"put_call_oi_ratio": 0.1, "spot_vs_flip_pct": None}  # way below history
    value, detail = features.options_flow_signal(chain_summary, history)
    assert detail["put_call_oi_zscore"] < 0
    assert value < 0


# --------------------------------------------------------------------- macro


def test_macro_features_signal_always_zero():
    signal, mult, _ = features.macro_features(["CPI"], 1, "day")
    assert signal == 0.0


def test_macro_features_confidence_floors_at_point_four():
    _, mult, _ = features.macro_features(["CPI", "FOMC", "NFP", "PCE"], 10, "day")
    assert mult == 0.4


def test_macro_features_no_events_full_confidence():
    _, mult, _ = features.macro_features([], 0, "day")
    assert mult == 1.0


# --------------------------------------------------------------------- sentiment


def test_sentiment_signal_unavailable_is_neutral_and_flagged():
    value, detail = features.sentiment_signal(None)
    assert value == 0.0
    assert detail["available"] is False


def test_sentiment_signal_extreme_fear_is_bullish_contrarian():
    value, _ = features.sentiment_signal(0.0)
    assert value == 1.0


def test_sentiment_signal_extreme_greed_is_bearish_contrarian():
    value, _ = features.sentiment_signal(100.0)
    assert value == -1.0


# --------------------------------------------------------------------- flow history persistence


def test_flow_history_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(features.config, "DATA_DIR", tmp_path)
    features.append_flow_snapshot("2026-09-01", "SPY", "day", 1.1, 2.0)
    features.append_flow_snapshot("2026-09-02", "SPY", "day", 1.3, -1.0)
    features.append_flow_snapshot("2026-09-01", "QQQ", "day", 5.0, 0.0)

    spy_day = features.load_flow_history("SPY", "day")
    assert len(spy_day) == 2
    assert list(spy_day["put_call_oi_ratio"]) == [1.1, 1.3]

    qqq_day = features.load_flow_history("QQQ", "day")
    assert len(qqq_day) == 1


def test_load_flow_history_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(features.config, "DATA_DIR", tmp_path)
    df = features.load_flow_history("SPY", "day")
    assert df.empty


def test_append_flow_snapshot_same_day_replaces(tmp_path, monkeypatch):
    monkeypatch.setattr(features.config, "DATA_DIR", tmp_path)
    features.append_flow_snapshot("2026-09-01", "SPY", "day", 1.1, 2.0)
    features.append_flow_snapshot("2026-09-01", "SPY", "week", 3.0, 1.0)
    features.append_flow_snapshot("2026-09-01", "SPY", "day", 1.5, 4.0)
    spy_day = features.load_flow_history("SPY", "day")
    assert len(spy_day) == 1
    assert spy_day.iloc[0]["put_call_oi_ratio"] == 1.5
    assert len(features.load_flow_history("SPY", "week")) == 1