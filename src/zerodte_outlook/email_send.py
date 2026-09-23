"""Gmail SMTP sending - adapted from newswire_terminal/src/bondwire/email_send.py."""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from tenacity import retry, stop_after_attempt, wait_exponential

from . import config

logger = logging.getLogger("zerodte_outlook.email")


@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
def _send(message: MIMEMultipart) -> None:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=config.HTTP_TIMEOUT_SECONDS) as smtp:
        smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
        smtp.send_message(message)


def send(subject: str, text_body: str, html_body: str) -> bool:
    if not config.GMAIL_ADDRESS or not config.GMAIL_APP_PASSWORD:
        logger.error("GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set; cannot send.")
        return False

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = config.GMAIL_ADDRESS
    message["To"] = config.RECIPIENT_EMAIL
    message.attach(MIMEText(text_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        _send(message)
        logger.info("Sent outlook email: %s", subject)
        return True
    except Exception:
        logger.exception("Failed to send outlook email after retries.")
        return False
