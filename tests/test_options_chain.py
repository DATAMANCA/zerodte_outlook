import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zerodte_outlook.sources import options_chain as oc  # noqa: E402


def test_bs_gamma_peaks_at_the_money_and_is_positive_off_it():
    # Black-Scholes gamma peaks at-the-money and is positive everywhere, but
    # is NOT symmetric in raw strike space (it's symmetric in log-moneyness,
    # around the forward price, not spot) - don't assert near-equality
    # between equidistant strikes, that's not how the math works.
    spot = 100.0
    g_atm = oc._bs_gamma(spot, 100.0, 0.20, 30 / 365)
    g_otm_call_side = oc._bs_gamma(spot, 105.0, 0.20, 30 / 365)
    g_otm_put_side = oc._bs_gamma(spot, 95.0, 0.20, 30 / 365)
    assert g_atm > 0
    assert g_otm_call_side > 0 and g_otm_put_side > 0
    assert g_atm > g_otm_call_side  # ATM gamma is the peak
    assert g_atm > g_otm_put_side


def test_bs_gamma_handles_degenerate_inputs_without_raising():
    assert oc._bs_gamma(100.0, 100.0, None, 30 / 365) == 0.0
    assert oc._bs_gamma(100.0, 100.0, 0.0, 30 / 365) == 0.0
    assert oc._bs_gamma(100.0, 100.0, 0.2, 0.0) == 0.0
    assert oc._bs_gamma(0.0, 100.0, 0.2, 30 / 365) == 0.0


def test_time_to_expiry_years_floors_at_min_hours():
    # An expiry that's already passed (or is "now") must not produce a
    # zero/negative T, which would blow up _bs_gamma's division.
    now = datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)
    t = oc._time_to_expiry_years(date(2026, 9, 23), now)
    assert t == oc.MIN_HOURS_TO_EXPIRY / (24 * 365)


def test_find_zero_gamma_flip_interpolates_the_sign_crossing():
    gex_by_strike = {95.0: 2.0, 100.0: -6.0, 105.0: 10.0}
    # cumulative: 95 -> 2, 100 -> -4, 105 -> 6 : crosses between 100 and 105
    flip = oc._find_zero_gamma_flip(gex_by_strike)
    assert 100.0 < flip < 105.0


def test_find_zero_gamma_flip_none_when_no_crossing():
    assert oc._find_zero_gamma_flip({95.0: 1.0, 100.0: 2.0}) is None
    assert oc._find_zero_gamma_flip({}) is None


def test_find_max_pain_picks_strike_minimizing_writer_payout():
    # Symmetric OI at 95/100/105 for both calls and puts -> total payout is
    # minimized at the center strike (500+500=1000) vs either extreme
    # (0+1500=1500), so max pain must land unambiguously at 100.
    calls = {95.0: {"oi": 100.0}, 100.0: {"oi": 100.0}, 105.0: {"oi": 100.0}}
    puts = {95.0: {"oi": 100.0}, 100.0: {"oi": 100.0}, 105.0: {"oi": 100.0}}
    assert oc._find_max_pain(calls, puts) == 100.0


def test_find_max_pain_none_when_empty():
    assert oc._find_max_pain({}, {}) is None
