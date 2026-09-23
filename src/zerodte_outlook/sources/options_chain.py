"""Today's SPY/QQQ options chain -> put/call ratios and a free-data GEX
(gamma exposure) proxy.

This is explicitly an APPROXIMATION, not a real dealer-positioning feed:
- yfinance's per-contract `impliedVolatility` feeds a Black-Scholes gamma we
  compute ourselves (stdlib `math` only, no scipy).
- The convention "dealers are net long calls / net short puts" (so GEX =
  call_gamma*call_OI - put_gamma*put_OI) is the standard simplification used
  by public/free GEX write-ups; a real dealer book can differ.
- The zero-gamma "flip" strike here is a cheaper proxy than a real one: a
  real flip re-prices gamma at many hypothetical spot levels across the whole
  chain; this version reuses gamma priced at the *current* spot and just
  sweeps cumulative GEX by strike. Good enough for a directional lean, not
  for precise level-trading.
This can't be backtested (yfinance only exposes *today's* chain) — see
backtest.py and the README for how live accuracy is tracked instead.
"""
import math
from datetime import date, datetime, timezone

import yfinance as yf

from . import market
from .market import DataError, _retry  # noqa: F401 - re-exported for callers

RISK_FREE_RATE = 0.05
# Options close ~4pm ET. 20:00 UTC approximates that during EDT (most of the
# year); it's off by an hour during EST, same accepted drift as the cron
# schedule elsewhere in this project.
MARKET_CLOSE_UTC_HOUR = 20
MIN_HOURS_TO_EXPIRY = 1.0  # floor so gamma doesn't blow up/flip sign pre-open


def _time_to_expiry_years(expiry: date, now_utc: datetime) -> float:
    close = datetime(expiry.year, expiry.month, expiry.day, MARKET_CLOSE_UTC_HOUR, tzinfo=timezone.utc)
    hours = max((close - now_utc).total_seconds() / 3600, MIN_HOURS_TO_EXPIRY)
    return hours / (24 * 365)


def _bs_gamma(spot: float, strike: float, iv: float | None, t_years: float) -> float:
    if not iv or iv <= 0 or t_years <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    try:
        d1 = (math.log(spot / strike) + (RISK_FREE_RATE + 0.5 * iv * iv) * t_years) / (iv * math.sqrt(t_years))
        return math.exp(-d1 * d1 / 2) / (math.sqrt(2 * math.pi) * spot * iv * math.sqrt(t_years))
    except (ValueError, ZeroDivisionError, OverflowError):
        return 0.0


def _strike_map(frame) -> dict[float, dict]:
    out = {}
    for row in frame.itertuples(index=False):
        k = float(row.strike)
        oi = float(row.openInterest) if row.openInterest == row.openInterest else 0.0  # NaN check
        vol = float(row.volume) if row.volume == row.volume else 0.0
        iv = float(row.impliedVolatility) if row.impliedVolatility == row.impliedVolatility else None
        out[k] = {"oi": oi, "volume": vol, "iv": iv}
    return out


def _find_zero_gamma_flip(gex_by_strike: dict[float, float]) -> float | None:
    if not gex_by_strike:
        return None
    cum = 0.0
    prev_k = prev_cum = None
    for k in sorted(gex_by_strike):
        cum += gex_by_strike[k]
        if prev_cum is not None and prev_cum < 0 <= cum:
            span = cum - prev_cum
            frac = (-prev_cum) / span if span else 0.0
            return prev_k + frac * (k - prev_k)
        prev_k, prev_cum = k, cum
    return None


def _find_max_pain(calls: dict[float, dict], puts: dict[float, dict]) -> float | None:
    strikes = sorted(set(calls) | set(puts))
    if not strikes:
        return None
    best_k, best_payout = None, None
    for settle in strikes:
        payout = 0.0
        for k, c in calls.items():
            payout += max(settle - k, 0.0) * c["oi"]
        for k, p in puts.items():
            payout += max(k - settle, 0.0) * p["oi"]
        if best_payout is None or payout < best_payout:
            best_k, best_payout = settle, payout
    return best_k


def _summarize_expiry(tk: yf.Ticker, ticker: str, spot: float, expiry: str) -> dict:
    oc = _retry(lambda: tk.option_chain(expiry), f"chain {ticker.upper()} {expiry}")
    calls, puts = _strike_map(oc.calls), _strike_map(oc.puts)
    t_years = _time_to_expiry_years(date.fromisoformat(expiry), datetime.now(timezone.utc))

    call_oi = sum(c["oi"] for c in calls.values())
    put_oi = sum(p["oi"] for p in puts.values())
    call_vol = sum(c["volume"] for c in calls.values())
    put_vol = sum(p["volume"] for p in puts.values())

    gex_by_strike = {}
    for k in sorted(set(calls) | set(puts)):
        c, p = calls.get(k, {"oi": 0, "iv": None}), puts.get(k, {"oi": 0, "iv": None})
        c_gamma = _bs_gamma(spot, k, c["iv"], t_years)
        p_gamma = _bs_gamma(spot, k, p["iv"], t_years)
        gex_by_strike[k] = (c_gamma * c["oi"] - p_gamma * p["oi"]) * 100 * spot * spot * 0.01

    zero_gamma_flip = _find_zero_gamma_flip(gex_by_strike)
    return {
        "expiry": expiry,
        "put_call_oi_ratio": (put_oi / call_oi) if call_oi else None,
        "put_call_vol_ratio": (put_vol / call_vol) if call_vol else None,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "total_gex": sum(gex_by_strike.values()),
        "zero_gamma_flip": zero_gamma_flip,
        "spot_vs_flip_pct": ((spot - zero_gamma_flip) / zero_gamma_flip * 100) if zero_gamma_flip else None,
        "max_pain": _find_max_pain(calls, puts),
    }


def get_chain_snapshot(ticker: str) -> dict:
    """Day summary (nearest expiry - true 0DTE when today is a trading day
    with a same-day expiry) and week summary (nearest Friday expiry)."""
    tk = yf.Ticker(ticker)
    spot = market.get_spot(ticker)
    exps = _retry(lambda: list(tk.options), f"expiries {ticker.upper()}")
    if not exps:
        raise DataError(f"{ticker.upper()} has no listed options")

    today = date.today()
    day_expiry = exps[0]
    friday_expiry = next(
        (e for e in exps if date.fromisoformat(e) >= today and date.fromisoformat(e).weekday() == 4),
        day_expiry,
    )

    day_summary = _summarize_expiry(tk, ticker, spot, day_expiry)
    week_summary = day_summary if friday_expiry == day_expiry else _summarize_expiry(tk, ticker, spot, friday_expiry)

    return {
        "ticker": ticker.upper(),
        "spot": spot,
        "zero_dte_available": date.fromisoformat(day_expiry) == today,
        "day": day_summary,
        "week": week_summary,
    }
