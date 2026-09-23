"""Weighted composite scoring: category signals -> Bullish/Bearish/Neutral +
confidence, per ticker and horizon (day/week).

`compute_composite` is deliberately generic (just a dict of category ->
signal, reweighted over whatever categories are actually present) so both
the live pipeline (all 5 categories) and the backtest (technical + iv_regime
+ macro only, see backtest.py) share the same weighting math.
"""
from . import config, features


def compute_composite(signals: dict[str, float | None], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    """Weighted mean of the available (non-None) signals, reweighted so
    missing/unavailable categories don't silently count as neutral."""
    available = {k: v for k, v in signals.items() if v is not None}
    total_w = sum(weights.get(k, 0.0) for k in available)
    if not available or total_w == 0:
        return 0.0, {}
    composite = sum(weights[k] * v for k, v in available.items()) / total_w
    used_weights = {k: weights[k] / total_w for k in available}
    return max(-1.0, min(1.0, composite)), used_weights


_THRESHOLDS = {
    "day": (config.DAY_BULLISH_THRESHOLD, config.DAY_BEARISH_THRESHOLD),
    "week": (config.WEEK_BULLISH_THRESHOLD, config.WEEK_BEARISH_THRESHOLD),
}


def label_for(composite: float, horizon: str) -> str:
    bullish, bearish = _THRESHOLDS[horizon]
    if composite >= bullish:
        return "Bullish"
    if composite <= bearish:
        return "Bearish"
    return "Neutral"


def _horizon_result(composite: float, horizon: str, used_weights: dict, confidence_multiplier: float, factors: dict) -> dict:
    return {
        "composite": composite,
        "label": label_for(composite, horizon),
        "confidence": round(max(0.0, min(1.0, abs(composite) * confidence_multiplier)), 3),
        "used_weights": used_weights,
        "factors": factors,
    }


def build_scorecard(
    ticker: str,
    market_history,
    gap_pct: float,
    vix_history,
    vix_term: dict,
    chain_snapshot: dict,
    today_events: list[str],
    week_count: int,
    fear_greed: float | None,
) -> dict:
    """Full live scorecard for one ticker: day + week, all 5 categories,
    with the factor breakdown needed for the email."""
    tech_day_val, tech_day_detail = features.technical_signal_day(market_history, gap_pct)
    tech_week_val, tech_week_detail = features.technical_signal_week(market_history)
    iv_val, iv_detail = features.iv_regime_signal(vix_history, vix_term)
    sent_val, sent_detail = features.sentiment_signal(fear_greed)
    sent_for_composite = sent_val if sent_detail["available"] else None

    flow_day_hist = features.load_flow_history(ticker, "day")
    flow_day_val, flow_day_detail = features.options_flow_signal(chain_snapshot["day"], flow_day_hist)
    flow_week_hist = features.load_flow_history(ticker, "week")
    flow_week_val, flow_week_detail = features.options_flow_signal(chain_snapshot["week"], flow_week_hist)

    macro_day_val, macro_day_mult, macro_day_detail = features.macro_features(today_events, week_count, "day")
    macro_week_val, macro_week_mult, macro_week_detail = features.macro_features(today_events, week_count, "week")

    # "macro" is deliberately NOT a composite input: its signal is always 0.0
    # (no free directional edge on a CPI/FOMC outcome), so including it here
    # would just dilute the composite toward 0 on every single day, not only
    # event days. Its only effect is macro_day_mult/macro_week_mult shrinking
    # confidence on days that actually have a high-impact event.
    day_composite, day_weights = compute_composite(
        {"technical": tech_day_val, "iv_regime": iv_val, "options_flow": flow_day_val,
         "sentiment": sent_for_composite},
        config.WEIGHTS_DAY,
    )
    week_composite, week_weights = compute_composite(
        {"technical": tech_week_val, "iv_regime": iv_val, "options_flow": flow_week_val,
         "sentiment": sent_for_composite},
        config.WEIGHTS_WEEK,
    )

    return {
        "ticker": ticker,
        "day": _horizon_result(day_composite, "day", day_weights, macro_day_mult, {
            "technical": tech_day_detail, "iv_regime": iv_detail,
            "options_flow": flow_day_detail, "macro": macro_day_detail, "sentiment": sent_detail,
        }),
        "week": _horizon_result(week_composite, "week", week_weights, macro_week_mult, {
            "technical": tech_week_detail, "iv_regime": iv_detail,
            "options_flow": flow_week_detail, "macro": macro_week_detail, "sentiment": sent_detail,
        }),
        "chain_snapshot": chain_snapshot,
    }
