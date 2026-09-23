import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from zerodte_outlook import predictions  # noqa: E402


def _history(dates, opens, closes):
    return pd.DataFrame({"Open": opens, "Close": closes}, index=pd.to_datetime(dates))


def test_append_new_creates_one_row_per_ticker_and_horizon():
    # Starts from an explicit empty frame, not predictions.load() - loading
    # the real on-disk log here would make this test's outcome depend on
    # whatever happens to be in the project's actual data file.
    empty = pd.DataFrame(columns=predictions.COLUMNS)
    scorecards = {
        "SPY": {"day": {"label": "Bullish", "composite": 0.5, "confidence": 0.5},
                "week": {"label": "Neutral", "composite": 0.0, "confidence": 0.0}},
    }
    df = predictions.append_new(empty, date(2026, 9, 23), scorecards)
    assert len(df) == 2
    assert set(df["horizon"]) == {"day", "week"}
    assert df.iloc[0]["resolved"] == False  # noqa: E712


def test_resolve_pending_never_resolves_same_day():
    # Regression test: yfinance can return a live, in-progress bar for
    # today's own date while the market is open - resolving against it would
    # score a prediction against a close that hasn't happened yet.
    today = date(2026, 9, 23)
    df = pd.DataFrame([{
        "date": today.isoformat(), "ticker": "SPY", "horizon": "day", "label": "Bullish",
        "composite": 0.5, "confidence": 0.5, "resolved": False, "actual_direction": None, "correct": None,
    }])
    hist = _history([today], [100.0], [105.0])  # would resolve "correct" if allowed to
    out = predictions.resolve_pending(df, {"SPY": hist}, as_of=today)
    assert out.iloc[0]["resolved"] == False  # noqa: E712
    assert pd.isna(out.iloc[0]["correct"])


def test_resolve_pending_day_uses_open_to_close():
    yesterday = date(2026, 9, 22)
    today = date(2026, 9, 23)
    df = pd.DataFrame([{
        "date": yesterday.isoformat(), "ticker": "SPY", "horizon": "day", "label": "Bullish",
        "composite": 0.5, "confidence": 0.5, "resolved": False, "actual_direction": None, "correct": None,
    }])
    hist = _history([yesterday, today], [100.0, 106.0], [105.0, 107.0])
    out = predictions.resolve_pending(df, {"SPY": hist}, as_of=today)
    assert out.iloc[0]["resolved"] == True  # noqa: E712
    assert out.iloc[0]["actual_direction"] == "Up"  # Close(105) > Open(100)
    assert out.iloc[0]["correct"] == True  # noqa: E712 - Bullish predicted, Up actual


def test_resolve_pending_week_needs_five_trading_days_forward():
    start = date(2026, 9, 1)
    dates = pd.date_range(start, periods=4, freq="B")  # only 3 trading days after start
    df = pd.DataFrame([{
        "date": start.isoformat(), "ticker": "SPY", "horizon": "week", "label": "Bullish",
        "composite": 0.5, "confidence": 0.5, "resolved": False, "actual_direction": None, "correct": None,
    }])
    hist = _history(dates, [100.0] * len(dates), [101.0] * len(dates))
    out = predictions.resolve_pending(df, {"SPY": hist}, as_of=date(2026, 9, 10))
    assert out.iloc[0]["resolved"] == False  # noqa: E712 - not enough forward data yet


def test_resolve_pending_neutral_label_has_no_correct_verdict():
    yesterday = date(2026, 9, 22)
    today = date(2026, 9, 23)
    df = pd.DataFrame([{
        "date": yesterday.isoformat(), "ticker": "SPY", "horizon": "day", "label": "Neutral",
        "composite": 0.05, "confidence": 0.05, "resolved": False, "actual_direction": None, "correct": None,
    }])
    hist = _history([yesterday, today], [100.0, 106.0], [105.0, 107.0])
    out = predictions.resolve_pending(df, {"SPY": hist}, as_of=today)
    assert out.iloc[0]["resolved"] == True  # noqa: E712
    assert pd.isna(out.iloc[0]["correct"])  # Neutral makes no call to score


def test_accuracy_summary_only_counts_resolved_directional_calls():
    df = pd.DataFrame([
        {"ticker": "SPY", "horizon": "day", "resolved": True, "correct": True},
        {"ticker": "SPY", "horizon": "day", "resolved": True, "correct": False},
        {"ticker": "SPY", "horizon": "day", "resolved": True, "correct": None},  # Neutral, excluded
        {"ticker": "SPY", "horizon": "day", "resolved": False, "correct": None},  # pending, excluded
    ])
    summary = predictions.accuracy_summary(df)
    assert summary[("SPY", "day")]["n"] == 2
    assert summary[("SPY", "day")]["hit_rate"] == 0.5
