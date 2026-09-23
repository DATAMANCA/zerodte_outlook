"""Market-wide sentiment: CNN's unofficial Fear & Greed Index.

This is a best-effort, optional source. It's an undocumented public endpoint
(no official API, no key) that CNN can change or block at any time — in
testing from this environment it returned a bot-detection block even with
browser-like headers, though it may work fine from a GitHub Actions runner.
Every caller must treat a None return as "unavailable" and continue without
it; nothing else in the pipeline depends on this succeeding.

A free, reliable CBOE market-wide put/call ratio download could not be
confirmed (Cboe's site offers no public CSV link outside paid DataShop), so
it's intentionally left out — options_chain.py's SPY/QQQ-specific put/call
OI ratio (from the live chain, not CBOE) covers that angle instead.
"""
import logging

import requests

FEAR_GREED_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

logger = logging.getLogger("zerodte_outlook.sentiment")


def get_fear_greed() -> float | None:
    """Current CNN Fear & Greed score, 0 (extreme fear) - 100 (extreme greed),
    or None if the endpoint is unavailable."""
    try:
        resp = requests.get(FEAR_GREED_URL, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        return float(payload["fear_and_greed"]["score"])
    except Exception:
        logger.info("Fear & Greed index unavailable this run.", exc_info=True)
        return None
