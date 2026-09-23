import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zerodte_outlook.sources import macro_calendar as mc  # noqa: E402


def test_nth_business_day_skips_weekend():
    # Sept 2026: Sept 1 = Tue, so 1st business day = Sept 1
    assert mc._nth_business_day(2026, 9, 1) == date(2026, 9, 1)
    # 3rd business day = Sept 3 (Thu)
    assert mc._nth_business_day(2026, 9, 3) == date(2026, 9, 3)


def test_pmi_dates_matches_known_september_2026():
    events = mc.pmi_dates(date(2026, 9, 1), date(2026, 9, 30))
    assert events[date(2026, 9, 1)] == ["ISM Mfg PMI"]
    assert events[date(2026, 9, 3)] == ["ISM Services PMI"]
    assert len(events) == 2  # nothing else in September


def test_flash_pmi_window_covers_sept_23_2026():
    # Regression test: 2026-09-23 (a real S&P Global Flash PMI day) must be
    # inside the flagged window - this is exactly the day the live email
    # missed before the fix.
    events = mc.flash_pmi_window_dates(date(2026, 9, 1), date(2026, 9, 30))
    assert date(2026, 9, 23) in events
    assert all(d.weekday() < 5 for d in events)  # weekdays only
    assert all(21 <= d.day <= 24 for d in events)


def test_fomc_dates_include_known_2026_meeting():
    assert date(2026, 9, 16) in mc.FOMC_MEETING_DATES


def test_high_impact_dates_combines_all_sources_without_api_key():
    # No FRED_API_KEY -> CPI/NFP/PCE silently skipped, but FOMC + PMI still work.
    events = mc.high_impact_dates("", date(2026, 9, 1), date(2026, 9, 30))
    assert "FOMC" in events[date(2026, 9, 16)]
    assert "ISM Mfg PMI" in events[date(2026, 9, 1)]


def test_today_and_week_flags_reports_todays_events():
    today_events, week_count = mc.today_and_week_flags("", date(2026, 9, 23))
    assert "S&P Flash PMI (approx.)" in today_events
    assert week_count >= 1
