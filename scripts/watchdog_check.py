"""Independent staleness check for the daily pipeline - adapted from
newswire_terminal/scripts/watchdog_check.py's pattern.

Deliberately scheduled as its OWN workflow (see .github/workflows/
watchdog.yml) rather than as an `if: failure()` step inside daily_outlook.yml
- a same-workflow failure hook can't catch the case where GitHub stops
scheduling that workflow entirely (silent auto-disable after long
inactivity, a bad cron edit, etc.). This checks the most recent date
actually committed to predictions_log.csv instead: if daily_outlook.yml runs
but fails before its commit step, or doesn't run at all, that date stops
advancing and this catches it either way.
"""
import logging
import smtplib
import sys
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zerodte_outlook import config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("zerodte_outlook.watchdog")

# The watchdog itself only runs on weekday afternoons (see watchdog.yml), so
# this only ever has to span a normal same-day gap (~11 hours from the
# ~7am ET main run to the ~6pm ET check) - it does not need to be
# weekend-aware itself.
STALE_THRESHOLD_HOURS = 30


def _last_run_date() -> date | None:
    if not config.PREDICTIONS_LOG_PATH.exists():
        return None
    last = None
    with open(config.PREDICTIONS_LOG_PATH, encoding="utf-8") as f:
        next(f, None)  # header
        for line in f:
            raw = line.split(",", 1)[0].strip()
            try:
                d = date.fromisoformat(raw)
            except ValueError:
                continue
            if last is None or d > last:
                last = d
    return last


def _send_alert(last_run: date | None) -> None:
    if last_run:
        body = f"No successful zerodte_outlook run has committed data since {last_run.isoformat()}."
    else:
        body = "zerodte_outlook has no recorded successful run at all (predictions_log.csv missing/empty)."
    body += "\n\nCheck the GitHub Actions tab for the daily_outlook.yml workflow."

    message = MIMEText(body)
    message["Subject"] = "zerodte_outlook watchdog: pipeline may be stalled"
    message["From"] = config.GMAIL_ADDRESS
    message["To"] = config.RECIPIENT_EMAIL

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=config.HTTP_TIMEOUT_SECONDS) as smtp:
        smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
        smtp.send_message(message)


def main() -> int:
    last_run = _last_run_date()
    if last_run is None:
        logger.warning("No prior successful run recorded yet; alerting.")
        _send_alert(None)
        return 0

    run_start = datetime.combine(last_run, datetime.min.time(), tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - run_start
    threshold = timedelta(hours=STALE_THRESHOLD_HOURS)

    if age > threshold:
        logger.warning("Last successful run was %s ago (threshold %s); alerting.", age, threshold)
        _send_alert(last_run)
    else:
        logger.info("Last successful run was %s ago; within threshold, no alert.", age)
    return 0


if __name__ == "__main__":
    sys.exit(main())
