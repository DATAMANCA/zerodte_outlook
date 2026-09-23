"""High-impact macro event calendar: FOMC (static, hand-maintained) + FRED
release dates for CPI / NFP / PCE (free API, historical and forward-looking)
+ ISM PMI (schedule-rule-based, see below).

FRED release IDs (https://fred.stlouisfed.org/releases):
  10 = Consumer Price Index (CPI)
  50 = Employment Situation (NFP / unemployment rate)
  54 = Personal Income and Outlays (PCE)

ISM PMI is intentionally NOT a FRED release: ISM's data was pulled from FRED
entirely in 2016 (licensing) and S&P Global's competing Flash PMI is a paid
product not on FRED either - there is no free source for the actual PMI
*value*. But both releases' SCHEDULEs are knowable for free without the data
feed itself - same trick as the FOMC static list, just computed by rule:
  - ISM Manufacturing/Services: official ISM policy fixes these at the 1st /
    3rd business day of the month - exact, not an approximation.
  - S&P Global Flash PMI: no published formula was found (their own release
    calendar blocks automated fetches) - confirmed empirically to land
    2026-09-23 (a Wednesday), consistent with the commonly reported "around
    the 22nd-24th" pattern. Modeled as a 21st-24th weekday WINDOW rather than
    a single exact day, since the precise rule isn't confirmed - this can
    over-flag by a few days a month, which only costs a mild, harmless extra
    confidence-dampening tick (see features.macro_features), not a wrong
    direction call.
None of these predict the PMI reading itself, only that a release is
scheduled (a confidence dampener, like every other macro event here); the
releases land mid-morning (~9:45-10am ET), after this tool's pre-market
email goes out, so the *value* is never knowable in advance regardless.
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


def _nth_business_day(year: int, month: int, n: int) -> date:
    """The n-th Mon-Fri weekday of the month (1-indexed). Doesn't account for
    US market holidays, so can land a day off around a holiday-adjacent
    month start (e.g. a New Year's Day on a weekday) - a minor, documented
    gap, not worth a full holiday calendar for a confidence dampener."""
    d = date(year, month, 1)
    count = 0
    while True:
        if d.weekday() < 5:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)


def pmi_dates(start: date, end: date) -> dict[date, list[str]]:
    """ISM Manufacturing (1st business day) and Services (3rd business day)
    PMI release dates by official ISM schedule policy."""
    events: dict[date, list[str]] = {}
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        for n, label in ((1, "ISM Mfg PMI"), (3, "ISM Services PMI")):
            d = _nth_business_day(y, m, n)
            if start <= d <= end:
                events.setdefault(d, []).append(label)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return events


def flash_pmi_window_dates(start: date, end: date) -> dict[date, list[str]]:
    """Weekdays 21st-24th of each month - an approximate window for S&P
    Global's Flash PMI, since its exact release-day rule isn't confirmed
    (see module docstring)."""
    events: dict[date, list[str]] = {}
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        for day in range(21, 25):
            try:
                d = date(y, m, day)
            except ValueError:
                continue
            if d.weekday() < 5 and start <= d <= end:
                events.setdefault(d, []).append("S&P Flash PMI (approx.)")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return events


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
    for d, labels in pmi_dates(start, end).items():
        events.setdefault(d, []).extend(labels)
    for d, labels in flash_pmi_window_dates(start, end).items():
        events.setdefault(d, []).extend(labels)
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
