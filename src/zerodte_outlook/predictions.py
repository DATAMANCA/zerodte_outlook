"""Self-tracked live accuracy log (predictions_log.csv).

This exists because the options-flow/gamma-exposure component can't be
backtested (see backtest.py) - so instead, every day's Day and Week
predictions are logged, and resolved against the actual outcome on a later
run once the relevant price data exists. GitHub Actions commits the updated
CSV back to the repo each run, so this compounds across days indefinitely.
"""
from datetime import date

import pandas as pd

from . import config

COLUMNS = ["date", "ticker", "horizon", "label", "composite", "confidence",
           "resolved", "actual_direction", "correct"]

_LABEL_TO_SIGN = {"Bullish": 1, "Bearish": -1, "Neutral": 0}
_SIGN_TO_DIRECTION = {1: "Up", -1: "Down", 0: "Flat"}


_OBJECT_COLUMNS = ["date", "ticker", "horizon", "label", "resolved", "actual_direction", "correct"]


def load() -> pd.DataFrame:
    if not config.PREDICTIONS_LOG_PATH.exists():
        df = pd.DataFrame(columns=COLUMNS)
    else:
        df = pd.read_csv(config.PREDICTIONS_LOG_PATH)
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = None
        df = df[COLUMNS]
    # A column that's all-blank on disk round-trips as float64 NaN, which
    # then raises a LossySetitemError the first time resolve_pending tries
    # to write a string/bool into it. Force these columns to stay object
    # dtype so later assignment always works regardless of what's on disk.
    for col in _OBJECT_COLUMNS:
        df[col] = df[col].astype(object).where(df[col].notna(), None)
    return df


def has_run_for(df: pd.DataFrame, day: date) -> bool:
    """True if predictions for `day` are already logged, i.e. that day's email already went out."""
    return bool((df["date"].astype(str) == day.isoformat()).any())


def save(df: pd.DataFrame) -> None:
    config.PREDICTIONS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(config.PREDICTIONS_LOG_PATH, index=False)


def append_new(df: pd.DataFrame, today: date, scorecards: dict) -> pd.DataFrame:
    new_rows = []
    for ticker, card in scorecards.items():
        for horizon in ("day", "week"):
            h = card[horizon]
            new_rows.append({
                "date": today.isoformat(), "ticker": ticker, "horizon": horizon,
                "label": h["label"], "composite": h["composite"], "confidence": h["confidence"],
                "resolved": False, "actual_direction": None, "correct": None,
            })
    if not new_rows:
        return df
    # A manual re-run on the same day replaces that day's rows rather than
    # duplicating them, so accuracy stats count each day once.
    df = df[df["date"].astype(str) != today.isoformat()]
    return pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)


def resolve_pending(df: pd.DataFrame, history_by_ticker: dict[str, pd.DataFrame], as_of: date | None = None) -> pd.DataFrame:
    """Fill in actual_direction/correct for any past prediction whose outcome
    is now knowable (today's bar finalized for Day, +5 trading days out for
    Week). Rows without enough forward data yet are left untouched.

    `as_of` (default: today) guards against resolving a row logged TODAY
    using today's own in-progress intraday bar - yfinance's "daily" history
    can include a live, not-yet-final bar for the current session while the
    market is open, which would score a prediction against a close that
    hasn't happened yet.
    """
    as_of = as_of or date.today()
    df = df.copy()
    pending = df.index[df["resolved"].isin([False, "False"]) | df["resolved"].isna()]
    for i in pending:
        row = df.loc[i]
        hist = history_by_ticker.get(row["ticker"])
        if hist is None:
            continue
        row_date = date.fromisoformat(row["date"])
        if row_date >= as_of:
            continue
        dates = [ts.date() for ts in hist.index]
        if row_date not in dates:
            continue
        pos = dates.index(row_date)

        if row["horizon"] == "day":
            actual_sign = _sign(hist["Close"].iloc[pos] - hist["Open"].iloc[pos])
        else:  # week
            if pos + 5 >= len(hist):
                continue
            actual_sign = _sign(hist["Close"].iloc[pos + 5] - hist["Close"].iloc[pos])

        predicted_sign = _LABEL_TO_SIGN[row["label"]]
        correct = (predicted_sign == actual_sign) if predicted_sign != 0 else None
        df.loc[i, "resolved"] = True
        df.loc[i, "actual_direction"] = _SIGN_TO_DIRECTION[actual_sign]
        df.loc[i, "correct"] = correct
    return df


def _sign(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def accuracy_summary(df: pd.DataFrame) -> dict:
    """{(ticker, horizon): {"n": resolved directional calls, "hit_rate": ...}}"""
    resolved = df[(df["resolved"] == True) & df["correct"].notna()]  # noqa: E712
    out = {}
    for (ticker, horizon), group in resolved.groupby(["ticker", "horizon"]):
        n = len(group)
        out[(ticker, horizon)] = {"n": n, "hit_rate": group["correct"].mean() if n else None}
    return out
