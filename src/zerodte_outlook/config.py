"""Central config: paths, secrets, and the knobs that shape scoring/backtest.

Local dev reads a `.env` file (gitignored); in GitHub Actions the same names
come in as repo secrets / workflow env vars, so this file works in both.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
LOG_PATH = ROOT / "logs" / "app.log"

PREDICTIONS_LOG_PATH = DATA_DIR / "predictions_log.csv"
BACKTEST_REPORT_PATH = DATA_DIR / "backtest_report.md"
LAST_EMAIL_PATH = DATA_DIR / "last_email.html"

TICKERS = ["SPY", "QQQ"]

# --- Email -----------------------------------------------------------------
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL") or GMAIL_ADDRESS

# --- Macro calendar ----------------------------------------------------------
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# --- HTTP / retry ------------------------------------------------------------
HTTP_TIMEOUT_SECONDS = 20
HTTP_RETRIES = 3
HTTP_BACKOFF_SEC = 1.5

# --- Feature lookback windows ------------------------------------------------
SMA_LONG_DAYS = 20  # min lookback for the week momentum blend & VIX regime avg
RSI_PERIOD_DAYS = 14
VIX_AVG_DAYS = 20
GAMMA_HISTORY_MIN_DAYS = 10   # min self-logged days before the options-flow
                              # z-score is considered calibrated

# --- Scoring weights ---------------------------------------------------------
# Each horizon's composite is a weighted sum of category signals in [-1, +1].
# Override any weight via env, e.g. ZDO_WEIGHT_DAY_TECHNICAL=0.5
def _w(name: str, default: float) -> float:
    return float(os.environ.get(f"ZDO_WEIGHT_{name}", default))


# "macro" deliberately has no weight here: it can't carry a directional
# signal (no free edge on a CPI/FOMC outcome), so it never enters the
# weighted composite - see scoring.build_scorecard. It still matters through
# DAY_CONFIDENCE_FLOOR/WEEK's multiplier, which shrinks confidence (not
# direction) on high-impact-event days.
WEIGHTS_DAY = {
    "technical": _w("DAY_TECHNICAL", 0.30),     # overnight gap, RSI
    "iv_regime": _w("DAY_IV_REGIME", 0.15),     # VIX level/percentile, term structure
    "options_flow": _w("DAY_OPTIONS_FLOW", 0.35),  # put/call ratio, GEX proxy, zero-gamma distance
    "sentiment": _w("DAY_SENTIMENT", 0.10),     # CBOE put/call, Fear & Greed
}

WEIGHTS_WEEK = {
    "technical": _w("WEEK_TECHNICAL", 0.40),     # 5/20-day trend
    "iv_regime": _w("WEEK_IV_REGIME", 0.15),
    "options_flow": _w("WEEK_OPTIONS_FLOW", 0.20),  # nearest-Friday chain positioning
    "sentiment": _w("WEEK_SENTIMENT", 0.10),
}

# Composite score (roughly -1..+1) -> label thresholds, tuned per horizon from
# the backtest: the day signal's accuracy rises with its own magnitude
# (higher threshold = fewer, better calls), while the week signal's accuracy
# is roughly flat regardless of magnitude, so it favors a lower threshold
# (maximize coverage since selectivity buys nothing there). Re-check both
# after options-flow live data accumulates - it may shift these.
DAY_BULLISH_THRESHOLD = float(os.environ.get("ZDO_DAY_BULLISH_THRESHOLD", 0.20))
DAY_BEARISH_THRESHOLD = float(os.environ.get("ZDO_DAY_BEARISH_THRESHOLD", -0.20))
WEEK_BULLISH_THRESHOLD = float(os.environ.get("ZDO_WEEK_BULLISH_THRESHOLD", 0.10))
WEEK_BEARISH_THRESHOLD = float(os.environ.get("ZDO_WEEK_BEARISH_THRESHOLD", -0.10))

# --- Backtest -----------------------------------------------------------------
BACKTEST_YEARS = int(os.environ.get("ZDO_BACKTEST_YEARS", 8))
