"""Orchestrates the daily pipeline: fetch -> score -> log -> render -> send.

    python -m zerodte_outlook.main             # fetches live data and sends the email
    python -m zerodte_outlook.main --dry-run   # same, but writes the email to
                                                # data/last_email.html instead of sending
    python -m zerodte_outlook.main --skip-if-already-ran
                                                # no-op if today's predictions are already
                                                # logged (used by the backup cron so it
                                                # doesn't send a second email)
    python -m zerodte_outlook.main --intraday   # hourly update: re-scores and sends,
                                                # but writes nothing to data/ so the
                                                # morning prediction stays the one that
                                                # accuracy tracking scores
"""
import argparse
import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config, email_send, features, predictions, report, scoring
from .sources import macro_calendar, market, options_chain, sentiment

logger = logging.getLogger("zerodte_outlook.main")


def _setup_logging() -> None:
    config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(config.LOG_PATH, encoding="utf-8")],
    )
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def _intraday_stamp() -> str:
    try:
        return datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M ET")
    except ZoneInfoNotFoundError:
        return datetime.now(timezone.utc).strftime("%H:%M UTC")


def run(dry_run: bool = False, skip_if_already_ran: bool = False, intraday: bool = False) -> None:
    today = date.today()
    logger.info("Starting run for %s (dry_run=%s, intraday=%s)", today, dry_run, intraday)

    if skip_if_already_ran and predictions.has_run_for(predictions.load(), today):
        logger.info("Predictions for %s already logged; email already sent today - skipping.", today)
        return

    market_history = {t: market.get_daily_history(t, years=1) for t in config.TICKERS}
    vix_history = market.get_daily_history(market.VIX_SYMBOL, years=1)
    vix_term = market.get_vix_term_structure()
    rates_dollar = market.get_rates_and_dollar()
    today_events, week_count = macro_calendar.today_and_week_flags(config.FRED_API_KEY, today)
    fear_greed = sentiment.get_fear_greed()

    scorecards = {}
    for ticker in config.TICKERS:
        gap_pct = market.get_overnight_gap_pct(ticker)
        chain_snapshot = options_chain.get_chain_snapshot(ticker)
        scorecards[ticker] = scoring.build_scorecard(
            ticker, market_history[ticker], gap_pct, vix_history, vix_term,
            chain_snapshot, today_events, week_count, fear_greed,
        )
        # Intraday runs don't record flow snapshots: the z-score baseline is
        # one pre-open reading per day, and mid-session readings aren't comparable.
        if not intraday:
            for horizon in ("day", "week"):
                summary = chain_snapshot[horizon]
                features.append_flow_snapshot(
                    today.isoformat(), ticker, horizon,
                    summary.get("put_call_oi_ratio"), summary.get("spot_vs_flip_pct"),
                )
        logger.info("%s scored: day=%s week=%s", ticker,
                    scorecards[ticker]["day"]["label"], scorecards[ticker]["week"]["label"])

    pred_log = predictions.load()
    if not intraday:
        pred_log = predictions.resolve_pending(pred_log, market_history, as_of=today)
        pred_log = predictions.append_new(pred_log, today, scorecards)
        predictions.save(pred_log)
    accuracy = predictions.accuracy_summary(pred_log)

    subject, text_body, html_body = report.render(
        today, scorecards, rates_dollar, today_events, week_count, fear_greed, accuracy)
    if intraday:
        subject = subject.replace("0DTE Outlook", f"0DTE Intraday {_intraday_stamp()}", 1)

    if dry_run:
        config.LAST_EMAIL_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.LAST_EMAIL_PATH.write_text(html_body, encoding="utf-8")
        logger.info("Dry run: wrote rendered email to %s (not sent)", config.LAST_EMAIL_PATH)
        print(text_body)
    else:
        ok = email_send.send(subject, text_body, html_body)
        if not ok:
            raise SystemExit(1)
    logger.info("Run complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-if-already-ran", action="store_true")
    parser.add_argument("--intraday", action="store_true")
    args = parser.parse_args()
    _setup_logging()
    run(dry_run=args.dry_run, skip_if_already_ran=args.skip_if_already_ran, intraday=args.intraday)
