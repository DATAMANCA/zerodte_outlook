"""High-impact macro event calendar: FOMC (static, hand-maintained) + FRED
release dates for CPI / NFP / PCE (free API, historical and forward-looking).

FRED release IDs (https://fred.stlouisfed.org/releases):
  10 = Consumer Price Index (CPI)
  50 = Employment Situation (NFP / unemployment rate)
  54 = Personal Income and Outlays (PCE)
"""
from datetime import date, timedelta

import requests

FRED_RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"

FRED_RELEASES = {
    "CPI": 10,
    "NFP": 50,
    "PCE": 54,
}

# Second-day (decision) dates of each regularly scheduled FOMC meeting.
# Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# The Fed publishes each year's tentative schedule roughly 1-2 years ahead;
# 2027 is already tentative-published as of 2026. UPDATE THIS LIST when it
# runs low on forward dates — meetings beyond it silently stop being flagged.
FOMC_MEETING_DATES = {
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1), date(2024, 6, 12),
    date(2024, 7, 31), date(2024, 9, 18), date(2024, 11, 7), date(2024, 12, 18),
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7), date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
    date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 9),
    date(2027, 7, 28), date(2027, 9, 15), date(2027, 10, 27), date(2027, 12, 8),
}


def fetch_release_dates(release_id: int, api_key: str, start: date, end: date) -> set[date]:
    """All historical/scheduled dates for a FRED release between start and end (inclusive)."""
    if not api_key:
        return set()
    params = {
        "release_id": release_id,
        "api_key": api_key,
        "file_type": "json",
        "realtime_start": start.isoformat(),
        "realtime_end": end.isoformat(),
        "include_release_dates_with_no_data": "true",
    }
    resp = requests.get(FRED_RELEASE_DATES_URL, params=params, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    if "error_message" in payload:
        raise RuntimeError(payload["error_message"])
    out = set()
    for row in payload.get("release_dates", []):
        try:
            out.add(date.fromisoformat(row["date"]))
        except (KeyError, ValueError):
            continue
    return out


def high_impact_dates(api_key: str, start: date, end: date) -> dict[date, list[str]]:
    """Map each date in [start, end] with at least one high-impact event to the
    list of event labels on it (e.g. {date(2026,9,16): ['FOMC']}).
    FRED calls fail independently and best-effort skip on error (no api_key,
    rate limit, etc.) rather than taking down the whole run.
    """
    events: dict[date, list[str]] = {}
    for d in FOMC_MEETING_DATES:
        if start <= d <= end:
            events.setdefault(d, []).append("FOMC")
    for label, release_id in FRED_RELEASES.items():
        try:
            dates = fetch_release_dates(release_id, api_key, start, end)
        except Exception:
            continue
        for d in dates:
            events.setdefault(d, []).append(label)
    return events


def today_and_week_flags(api_key: str, today: date) -> tuple[list[str], int]:
    """(today's event labels, count of high-impact days in the rest of this
    trading week including today). Week = today through the upcoming Friday.
    """
    friday = today + timedelta(days=(4 - today.weekday()) % 7)
    events = high_impact_dates(api_key, today, friday)
    today_events = events.get(today, [])
    week_count = len(events)
    return today_events, week_count
