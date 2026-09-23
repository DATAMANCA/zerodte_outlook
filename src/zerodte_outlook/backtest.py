"""Historical validation of the technical + IV-regime + macro-calendar
components only. The options-flow/gamma-exposure component CANNOT be
backtested with free data (yfinance only exposes today's chain, not
historical chains) — its live accuracy is tracked separately, see
predictions_log.csv and main.py. Not part of the daily pipeline; run
standalone:

    python -m zerodte_outlook.backtest

"Week" direction is a simplification: the actual outcome for every row is
the close FORWARD_DAYS_WEEK trading days later vs today's close (a rolling
5-trading-day-ahead return), not strictly "this calendar week's Friday" —
simpler to compute correctly across the whole history and equivalent in
spirit.
"""
import logging

import numpy as np
import pandas as pd

from . import config, features, scoring
from .sources import macro_calendar, market

logger = logging.getLogger("zerodte_outlook.backtest")

FORWARD_DAYS_WEEK = 5


def _vix_regime(vix_avg: float) -> str:
    if vix_avg != vix_avg:  # NaN
        return "unknown"
    if vix_avg < 15:
        return "low"
    if vix_avg < 25:
        return "normal"
    return "high"


def _score_one_day(df: pd.DataFrame, vix_hist: pd.DataFrame, events: dict, i: int) -> dict:
    today = df.index[i]
    window = df.iloc[: i + 1]
    gap_pct = (df["Open"].iloc[i] - df["Close"].iloc[i - 1]) / df["Close"].iloc[i - 1] * 100

    vix_window = vix_hist[vix_hist.index <= today]
    vix_avg = vix_window["Close"].tail(config.VIX_AVG_DAYS).mean()
    vix_term = {"vix": float(df["vix_close"].iloc[i]), "vix9d": None, "ratio": None}
    iv_val, _ = features.iv_regime_signal(vix_window, vix_term)

    tech_day_val, _ = features.technical_signal_day(window, gap_pct)
    tech_week_val, _ = features.technical_signal_week(window)

    d = today.date()
    today_events = events.get(d, [])
    friday = d + pd.Timedelta(days=(4 - d.weekday()) % 7)
    week_count = sum(1 for ev_date in events if d <= ev_date <= friday)
    _, macro_day_mult, _ = features.macro_features(today_events, week_count, "day")
    _, macro_week_mult, _ = features.macro_features(today_events, week_count, "week")

    # macro excluded from the composite itself (always 0 signal - see
    # scoring.build_scorecard's comment); only its confidence multiplier is used.
    day_composite, _ = scoring.compute_composite(
        {"technical": tech_day_val, "iv_regime": iv_val}, config.WEIGHTS_DAY)
    week_composite, _ = scoring.compute_composite(
        {"technical": tech_week_val, "iv_regime": iv_val}, config.WEIGHTS_WEEK)

    # Rest-of-session return (Open[i] -> Close[i]), NOT Close[i-1] -> Close[i].
    # The day composite is dominated by the gap itself (see
    # technical_signal_day), so comparing against the whole day's return
    # would silently re-score the already-known gap as if it were a
    # prediction, inflating the hit rate. Open->Close is what's actually
    # still unknown/tradeable once the gap is visible.
    actual_day = float(np.sign(df["Close"].iloc[i] - df["Open"].iloc[i]))
    actual_week = float(np.sign(df["Close"].iloc[i + FORWARD_DAYS_WEEK] - df["Close"].iloc[i]))

    return {
        "date": d, "vix_regime": _vix_regime(vix_avg),
        "day_pred": scoring.label_for(day_composite, "day"), "day_composite": day_composite,
        "day_conf": abs(day_composite) * macro_day_mult, "actual_day": actual_day,
        "week_pred": scoring.label_for(week_composite, "week"), "week_composite": week_composite,
        "week_conf": abs(week_composite) * macro_week_mult, "actual_week": actual_week,
    }


def _hit_rate(rdf: pd.DataFrame, pred_col: str, actual_col: str) -> tuple[float, int]:
    directional = rdf[rdf[pred_col] != "Neutral"]
    if directional.empty:
        return float("nan"), 0
    pred_sign = directional[pred_col].map({"Bullish": 1, "Bearish": -1})
    hits = int((pred_sign == directional[actual_col]).sum())
    return hits / len(directional), len(directional)


def _ticker_report(ticker: str, rdf: pd.DataFrame) -> str:
    out = [f"\n## {ticker}\n"]
    for horizon, pred_col, actual_col in [("Day", "day_pred", "actual_day"), ("Week", "week_pred", "actual_week")]:
        rate, n = _hit_rate(rdf, pred_col, actual_col)
        rate_str = f"{rate:.1%}" if rate == rate else "n/a"
        out.append(f"- **{horizon} hit rate**: {rate_str} over {n} directional calls "
                    f"(of {len(rdf)} total days; Neutral calls excluded)")
        for regime in ("low", "normal", "high"):
            sub = rdf[rdf["vix_regime"] == regime]
            r, n2 = _hit_rate(sub, pred_col, actual_col)
            if n2:
                out.append(f"  - {regime} VIX regime: {r:.1%} over {n2} calls")
    naive_edge = (rdf["day_composite"].apply(np.sign) * rdf["actual_day"]).mean()
    out.append(f"- Naive day directional edge (avg sign match, -1..+1, >0 means the "
               f"sign of the composite tends to agree with the actual move): {naive_edge:.3f}")
    return "\n".join(out)


def run(years: int | None = None, tickers: list[str] | None = None) -> str:
    years = years or config.BACKTEST_YEARS
    tickers = tickers or config.TICKERS

    vix_hist = market.get_daily_history(market.VIX_SYMBOL, years=years)
    events = macro_calendar.high_impact_dates(
        config.FRED_API_KEY, vix_hist.index[0].date(), vix_hist.index[-1].date())
    if not config.FRED_API_KEY:
        logger.warning("FRED_API_KEY not set; macro component only reflects FOMC, not CPI/NFP/PCE.")

    lines = [
        f"# Backtest report ({years}y, technical + IV-regime + macro only)\n",
        f"Generated for: {', '.join(tickers)}\n",
        "Options-flow/gamma-exposure cannot be backtested with free data "
        "(yfinance only exposes today's chain) - see README. Its live "
        "accuracy is tracked separately in predictions_log.csv once the "
        "daily pipeline has been running a while.\n",
    ]

    for ticker in tickers:
        hist = market.get_daily_history(ticker, years=years)
        df = hist.join(vix_hist["Close"].rename("vix_close"), how="inner")
        rows = [
            _score_one_day(df, vix_hist, events, i)
            for i in range(config.SMA_LONG_DAYS, len(df) - FORWARD_DAYS_WEEK)
        ]
        rdf = pd.DataFrame(rows)
        lines.append(_ticker_report(ticker, rdf))

    report = "\n".join(lines)
    config.BACKTEST_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.BACKTEST_REPORT_PATH.write_text(report, encoding="utf-8")
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
