# zerodte_outlook

A daily morning email with a rules-based directional bias for SPY & QQQ, for
both **today** (0DTE) and **this week**, built from free data sources only.

> Paper/analysis tool only. Not investment advice. See Limitations below -
> especially the options-flow section, which cannot be backtested.

## Setup

```bat
cd zerodte_outlook
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
:: fill in GMAIL_APP_PASSWORD and FRED_API_KEY in .env (both free)
```

- Gmail App Password: Google Account -> Security -> 2-Step Verification ->
  App passwords. Needed to send mail; never your normal Gmail password.
- FRED API key: free instant signup at
  https://fred.stlouisfed.org/docs/api/api_key.html. Needed for the CPI/NFP/
  PCE release-date part of the macro calendar (FOMC dates work without it).

## Run

```bat
:: local dry run - fetches live data, scores it, writes data/last_email.html
:: WITHOUT sending anything (no Gmail credentials needed for this)
set PYTHONPATH=src
.venv\Scripts\python -m zerodte_outlook.main --dry-run

:: real send (needs GMAIL_APP_PASSWORD set)
.venv\Scripts\python -m zerodte_outlook.main

:: backtest (technical + IV-regime + macro only - see Limitations)
.venv\Scripts\python -m zerodte_outlook.backtest
```

In production this runs on a schedule via GitHub Actions
(`.github/workflows/daily_outlook.yml`), not locally - see that file and its
required repo secrets (`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`,
`RECIPIENT_EMAIL`, `FRED_API_KEY`).

## How the score works

Each ticker gets two composites, **Day** and **Week**, each a weighted
average of category signals in [-1, +1] (weights in `config.py`,
env-overridable):

| Category | What it measures | Source |
|---|---|---|
| Technical | Overnight futures gap (vol-normalized) + RSI mean-reversion nudge (Day); 5d/20d SMA slope (Week) | `sources/market.py` |
| IV regime | VIX vs its 20d average, VIX9D/VIX term-structure inversion | `sources/market.py` |
| Options flow | Put/call OI ratio (self-calibrated z-score), spot vs the zero-gamma flip strike, max pain | `sources/options_chain.py` |
| Sentiment | CNN Fear & Greed (contrarian) | `sources/sentiment.py` |
| Macro | **Not** a composite input (see below) | `sources/macro_calendar.py` |

**Macro is a confidence dampener, not a signal.** There's no free way to
predict a CPI or FOMC *outcome*, so a high-impact event day doesn't push the
score bullish or bearish - it just shrinks confidence (down to a floor of
0.4x) on days/weeks that have one, reflecting that those are harder to call.

The composite -> Bullish/Bearish/Neutral thresholds are tuned per horizon
(`DAY_BULLISH_THRESHOLD` etc. in `config.py`) - the day signal's accuracy
rises with its own magnitude, so it uses a higher, more selective threshold;
the week signal's accuracy is roughly flat regardless of magnitude, so it
uses a lower one to maximize coverage instead.

## Backtest results (8y, SPY & QQQ, as of the last tuning pass)

| | Day hit rate | Day coverage | Week hit rate | Week coverage |
|---|---|---|---|---|
| SPY | 60.4% | 39% of days | 55.1% | 91% of days |
| QQQ | 57.6% | 39% of days | 55.7% | 93% of days |

"Day" hit rate is measured as: given the score known before/at the open,
does the *rest* of the session (open -> close) go the called direction -
not the whole day including the already-known gap, which would inflate the
number. "Week" is a rolling 5-trading-day-forward close, not strictly the
calendar Friday. Full methodology and per-VIX-regime breakdown in
`backtest.py` and `data/backtest_report.md` (regenerate with `python -m
zerodte_outlook.backtest`).

## Limitations

- **Options-flow/GEX cannot be backtested.** yfinance only exposes *today's*
  option chain, not historical chains - there's no free way to know what
  Tuesday's put/call ratio or gamma exposure looked like a year ago. The
  numbers above exclude that component entirely. Its live accuracy is
  instead self-tracked in `data/predictions_log.csv`, resolved a day (Day)
  or 5 trading days (Week) after each prediction, and surfaced in the email
  once enough history accumulates (`GAMMA_HISTORY_MIN_DAYS` in `config.py`).
  This is genuinely the biggest lever for 0DTE specifically, and it's also
  the one piece this tool can't prove in advance - watch the live track
  record before trusting it.
- **The GEX proxy is an approximation**, not a real dealer-positioning feed
  (see `sources/options_chain.py` docstring for the exact simplifications:
  Black-Scholes gamma from yfinance's own IV field, a "dealers long calls /
  short puts" convention, and a cheaper zero-gamma-flip calc than a real one
  that re-prices across hypothetical spot levels).
- **CNN Fear & Greed is unofficial** and can be blocked/changed without
  notice; the pipeline degrades gracefully (reweights around it) if it
  fails.
- **FOMC dates are a hand-maintained static list** (`sources/
  macro_calendar.py`) covering 2024-2027 as published by the Fed - update it
  when it runs low on forward dates.
- Free data (yfinance) is unofficial, unauthenticated, and can be delayed or
  rate-limited.
