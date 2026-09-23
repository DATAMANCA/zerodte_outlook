"""yfinance wrapper for price history and cross-asset spot levels.
Unofficial, unauthenticated, rate-limited — every call retries with backoff
(pattern borrowed from options_terminal/marketdata.py).
"""
import logging
import math
import time

import pandas as pd
import yfinance as yf

from .. import config

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# A ticker can be quoted under more than one free symbol; try each in order.
DOLLAR_INDEX_SYMBOLS = ["DX-Y.NYB", "DX=F", "UUP"]
FUTURES_SYMBOL = {"SPY": "ES=F", "QQQ": "NQ=F"}
VIX_SYMBOL = "^VIX"
VIX9D_SYMBOL = "^VIX9D"
TNX_SYMBOL = "^TNX"


class DataError(RuntimeError):
    pass


def _retry(fn, what: str):
    last = None
    for attempt in range(config.HTTP_RETRIES):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - yfinance raises all sorts
            last = exc
            time.sleep(config.HTTP_BACKOFF_SEC * (attempt + 1))
    raise DataError(f"{what}: {last}")


def _num(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f


def get_spot(ticker: str) -> float:
    tk = yf.Ticker(ticker)

    def _fetch():
        fi = tk.fast_info
        for key in ("lastPrice", "last_price", "regularMarketPrice"):
            try:
                v = fi[key]
            except (KeyError, TypeError):
                v = getattr(fi, key, None)
            if _num(v):
                return float(v)
        hist = tk.history(period="5d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
        raise DataError("no spot price available")

    return _retry(_fetch, f"spot {ticker.upper()}")


def get_spot_any(symbols: list[str]) -> tuple[float, str]:
    """Try each symbol in order (some free tickers are flaky/renamed);
    return (value, symbol_used) from the first that works."""
    last = None
    for sym in symbols:
        try:
            return get_spot(sym), sym
        except DataError as exc:
            last = exc
    raise DataError(f"no symbol in {symbols} usable: {last}")


def get_daily_history(ticker: str, years: int | None = None, period: str | None = None) -> pd.DataFrame:
    """Daily OHLCV, oldest first. Pass either `years` or a raw yfinance
    `period` string (e.g. '5d')."""
    tk = yf.Ticker(ticker)
    p = period or f"{years}y"

    def _fetch():
        hist = tk.history(period=p, interval="1d", auto_adjust=False)
        if hist.empty:
            raise DataError(f"no history for {ticker.upper()}")
        # Different tickers come back tz-aware in different exchange timezones
        # (e.g. SPY in America/New_York, ^VIX in America/Chicago) even though
        # both are daily US-session bars for the same trading day. Strip tz so
        # joins/comparisons across tickers align on the calendar date.
        hist.index = hist.index.tz_localize(None)
        return hist

    return _retry(_fetch, f"history {ticker.upper()} ({p})")


def get_last_close_and_change(ticker: str) -> tuple[float, float]:
    """(last close, 1-day change vs prior close) from the last few daily bars."""
    hist = get_daily_history(ticker, period="5d")
    closes = hist["Close"].dropna()
    if len(closes) < 2:
        raise DataError(f"not enough history for {ticker.upper()} to compute a change")
    last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
    return last, last - prev


def get_overnight_gap_pct(underlying: str) -> float:
    """Overnight gap estimate for `underlying` (e.g. SPY), from its futures
    proxy's OWN live price vs its OWN most recent daily settlement (e.g. ES=F
    now vs ES=F's prior close) - NOT the futures' absolute level vs the
    underlying's close. ES/NQ track the S&P 500 / Nasdaq-100 INDEX level,
    which trades at a different scale than the SPY/QQQ ETF price (~10x and
    ~40x respectively), so comparing levels directly would be nonsense; only
    the futures' own percent change transfers across that scale gap."""
    fut_symbol = FUTURES_SYMBOL[underlying]
    fut_now = get_spot(fut_symbol)
    fut_hist = get_daily_history(fut_symbol, period="5d")
    fut_prior_close = float(fut_hist["Close"].dropna().iloc[-1])
    if fut_prior_close == 0:
        raise DataError(f"prior settlement for {fut_symbol} is 0")
    return (fut_now - fut_prior_close) / fut_prior_close * 100


def get_vix_term_structure() -> dict:
    vix = get_spot(VIX_SYMBOL)
    vix9d = get_spot(VIX9D_SYMBOL)
    return {"vix": vix, "vix9d": vix9d, "ratio": vix9d / vix if vix else None}


def get_rates_and_dollar() -> dict:
    tnx, tnx_chg = get_last_close_and_change(TNX_SYMBOL)
    dxy_symbols_hist = DOLLAR_INDEX_SYMBOLS
    dxy = dxy_chg = None
    used = None
    for sym in dxy_symbols_hist:
        try:
            dxy, dxy_chg = get_last_close_and_change(sym)
            used = sym
            break
        except DataError:
            continue
    return {"tnx": tnx, "tnx_chg": tnx_chg, "dxy": dxy, "dxy_chg": dxy_chg, "dxy_symbol": used}
