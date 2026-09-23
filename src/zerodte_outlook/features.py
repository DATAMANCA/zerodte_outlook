"""Raw data -> normalized signals in [-1, +1] per scoring category.

Every `*_signal` function returns (value, detail_dict) where `value` is the
normalized signal (or None if the input was unusable) and `detail_dict` holds
the raw numbers behind it, for the email's factor breakdown.

Macro is special: we have no free way to predict a CPI/FOMC *outcome*, so its
"signal" is always 0 (no directional edge) — what it actually does is lower
the day/week's confidence via `macro_confidence_multiplier`, applied in
scoring.py after the weighted sum, not as part of it.
"""
import csv
import math
from pathlib import Path

import pandas as pd

from . import config

# --- Technical ---------------------------------------------------------------


def _rsi(closes: pd.Series, period: int) -> float | None:
    """Simple (non-Wilder-smoothed) RSI — fine for a directional lean, not a
    precision indicator."""
    if len(closes) < period + 1:
        return None
    delta = closes.diff().dropna()
    gains = delta.clip(lower=0).tail(period)
    losses = (-delta.clip(upper=0)).tail(period)
    avg_gain, avg_loss = gains.mean(), losses.mean()
    if avg_gain == 0 and avg_loss == 0:
        return 50.0  # genuinely no movement at all - neutral, not "overbought"
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _realized_vol_pct(closes: pd.Series, window: int = 20) -> float | None:
    """Std dev of daily % returns over `window` days - same units as gap_pct,
    so a gap can be measured in "how many normal days' worth of move" rather
    than a fixed cutoff that means different things in calm vs turbulent
    markets."""
    rets = closes.pct_change().dropna().tail(window) * 100
    if len(rets) < window // 2:
        return None
    vol = rets.std()
    return vol if vol and vol == vol else None  # None on NaN/0


def technical_signal_day(history: pd.DataFrame, gap_pct: float) -> tuple[float, dict]:
    """Overnight futures gap (dominant), normalized by recent realized
    volatility rather than a fixed clip - a 1.5% gap is unremarkable in a
    high-VIX regime but huge in a calm one. Falls back to a fixed 1.5% clip
    if there isn't enough history to measure volatility. Blended with a
    small RSI mean-reversion nudge."""
    closes = history["Close"].dropna()
    rsi = _rsi(closes, config.RSI_PERIOD_DAYS)
    vol = _realized_vol_pct(closes, config.VIX_AVG_DAYS)
    if vol:
        gap_component = max(-1.0, min(1.0, gap_pct / (2.0 * vol)))  # a 2-sigma gap maxes this out
    else:
        gap_component = max(-1.0, min(1.0, gap_pct / 1.5))
    rsi_component = 0.0
    if rsi is not None:
        if rsi >= 70:
            rsi_component = -(rsi - 70) / 30  # overbought -> mild bearish nudge
        elif rsi <= 30:
            rsi_component = (30 - rsi) / 30    # oversold -> mild bullish nudge
    value = max(-1.0, min(1.0, 0.85 * gap_component + 0.15 * rsi_component))
    return value, {"gap_pct": gap_pct, "rsi14": rsi, "realized_vol_pct": vol}


def technical_signal_week(history: pd.DataFrame) -> tuple[float | None, dict]:
    """5-day vs 20-day SMA slope, normalized by the 20-day SMA level.

    A multi-horizon (3/10/20-day) vol-adjusted momentum blend was tried here
    and A/B-tested in backtest.py: it was statistically indistinguishable
    from this simpler version (SPY flat, QQQ ~1pp worse, within one standard
    error on ~1700 samples) while cutting call coverage and adding
    complexity, so it wasn't kept. The backtest's own diagnostic (week hit
    rate doesn't improve with more selective/higher-magnitude thresholds -
    see the threshold sensitivity note) suggests the real ceiling here is
    about what a *free* weekly trend read can offer, not this particular
    formula - kept simple rather than chasing an illusory gain."""
    closes = history["Close"].dropna()
    if len(closes) < config.SMA_LONG_DAYS:
        return None, {"reason": "insufficient history"}
    sma_short = closes.tail(5).mean()
    sma_long = closes.tail(config.SMA_LONG_DAYS).mean()
    if sma_long == 0:
        return None, {"reason": "sma_long is 0"}
    pct_diff = (sma_short - sma_long) / sma_long * 100
    value = max(-1.0, min(1.0, pct_diff / 2.0))  # a 2% short/long SMA gap maxes this out
    return value, {"sma_short": sma_short, "sma_long": sma_long, "pct_diff": pct_diff}


# --- IV regime -----------------------------------------------------------------


def iv_regime_signal(vix_history: pd.DataFrame, vix_term: dict) -> tuple[float, dict]:
    """VIX above its trailing average and/or VIX9D>VIX (short-term fear
    spiking above longer-dated) both lean bearish/risk-off; the inverse
    leans bullish/risk-on. A lean, not a law — vol spikes also precede
    bounces, which the technical/options-flow components can offset."""
    closes = vix_history["Close"].dropna().tail(config.VIX_AVG_DAYS)
    avg = closes.mean() if len(closes) else None
    vix, vix9d = vix_term["vix"], vix_term["vix9d"]

    level_component = 0.0
    if avg:
        pct_above_avg = (vix - avg) / avg * 100
        level_component = -max(-1.0, min(1.0, pct_above_avg / 15.0))  # 15% above avg maxes it out

    term_component = 0.0
    ratio = vix_term.get("ratio")
    if ratio:
        term_component = -max(-1.0, min(1.0, (ratio - 1.0) / 0.15))  # inverted 15% maxes it out

    value = max(-1.0, min(1.0, 0.6 * level_component + 0.4 * term_component))
    return value, {"vix": vix, "vix9d": vix9d, "vix_20d_avg": avg, "term_ratio": ratio}


# --- Options flow (live-only, self-calibrating) --------------------------------

_FLOW_HISTORY_COLUMNS = ["date", "ticker", "horizon", "put_call_oi_ratio", "spot_vs_flip_pct"]


def _flow_history_path() -> Path:
    return config.DATA_DIR / "options_flow_history.csv"


def load_flow_history(ticker: str, horizon: str) -> pd.DataFrame:
    path = _flow_history_path()
    if not path.exists():
        return pd.DataFrame(columns=_FLOW_HISTORY_COLUMNS)
    df = pd.read_csv(path)
    return df[(df["ticker"] == ticker) & (df["horizon"] == horizon)]


def append_flow_snapshot(today_iso: str, ticker: str, horizon: str, pc_ratio: float | None, spot_vs_flip: float | None) -> None:
    path = _flow_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_FLOW_HISTORY_COLUMNS)
        if is_new:
            w.writeheader()
        w.writerow({"date": today_iso, "ticker": ticker, "horizon": horizon,
                    "put_call_oi_ratio": pc_ratio, "spot_vs_flip_pct": spot_vs_flip})


def _zscore(value: float | None, series: pd.Series) -> float | None:
    if value is None or len(series) < config.GAMMA_HISTORY_MIN_DAYS:
        return None
    std = series.std()
    if not std or math.isnan(std):
        return None
    return (value - series.mean()) / std


def options_flow_signal(chain_summary: dict, flow_history: pd.DataFrame) -> tuple[float, dict]:
    """Scored ingredient: put/call OI ratio z-score, treated as CONTRARIAN
    (an unusually put-heavy book vs our own recent norm leans bullish, and
    vice versa) - the traditional retail-sentiment reading of extreme
    put/call skew.

    Shown but NOT currently scored: spot vs. the zero-gamma flip strike
    (above flip = dealers long gamma => dampened/pinning; below flip =
    dealers short gamma => amplifying whatever direction price is already
    moving). This is directionally meaningful but as a MODIFIER on other
    signals (it says "trust the gap more/less"), not a standalone additive
    term - properly wiring that in needs a composite-math change beyond a
    simple weighted sum, so for now it's surfaced in the email's factor
    breakdown for you to read qualitatively, not folded into the composite.
    """
    pc_ratio = chain_summary.get("put_call_oi_ratio")
    spot_vs_flip = chain_summary.get("spot_vs_flip_pct")

    pc_z = _zscore(pc_ratio, flow_history["put_call_oi_ratio"].dropna()) if len(flow_history) else None
    # contrarian: a put-heavy book (pc_z > 0) leans bullish, not bearish.
    pc_component = 0.0 if pc_z is None else max(-1.0, min(1.0, pc_z / 2.0))

    value = max(-1.0, min(1.0, pc_component))
    return value, {
        "put_call_oi_ratio": pc_ratio, "put_call_oi_zscore": pc_z,
        "spot_vs_flip_pct": spot_vs_flip,
        "zero_gamma_flip": chain_summary.get("zero_gamma_flip"),
        "max_pain": chain_summary.get("max_pain"),
        "calibrated": pc_z is not None,
    }


# --- Macro -----------------------------------------------------------------------


def macro_features(today_events: list[str], week_count: int, horizon: str) -> tuple[float, float, dict]:
    """Always returns signal=0.0 (no free directional edge on a macro
    outcome). `confidence_multiplier` in (0, 1] shrinks toward 0 the more
    high-impact events sit in the relevant window — this is what actually
    expresses "today is riskier to call"."""
    n = len(today_events) if horizon == "day" else week_count
    multiplier = max(0.4, 1.0 - 0.25 * n)
    return 0.0, multiplier, {"events": today_events if horizon == "day" else None, "week_event_count": week_count}


# --- Sentiment ---------------------------------------------------------------


def sentiment_signal(fear_greed: float | None) -> tuple[float, dict]:
    """Contrarian: extreme fear (near 0) leans bullish, extreme greed (near
    100) leans bearish. Returns 0 (neutral, unavailable) if the endpoint
    failed — scoring.py excludes unavailable categories from the weighted
    sum instead of silently treating them as a real neutral reading."""
    if fear_greed is None:
        return 0.0, {"fear_greed": None, "available": False}
    value = max(-1.0, min(1.0, (50 - fear_greed) / 50))
    return value, {"fear_greed": fear_greed, "available": True}
