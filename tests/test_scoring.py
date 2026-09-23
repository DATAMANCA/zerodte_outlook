import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zerodte_outlook import config, scoring  # noqa: E402


def test_compute_composite_weighted_average():
    composite, used = scoring.compute_composite(
        {"a": 1.0, "b": -1.0}, {"a": 3.0, "b": 1.0})
    # (3*1 + 1*-1) / 4 = 0.5
    assert composite == 0.5
    assert used == {"a": 0.75, "b": 0.25}


def test_compute_composite_excludes_none_and_reweights():
    # "b" missing entirely -> only "a" counts, fully weighted despite its
    # config weight being less than 1.0 - this is the fix for the macro-
    # dilution bug: an absent/unavailable category must not silently shrink
    # the composite toward 0.
    composite, used = scoring.compute_composite(
        {"a": 0.8}, {"a": 0.3, "b": 0.7})
    assert composite == 0.8
    assert used == {"a": 1.0}


def test_compute_composite_empty_returns_neutral():
    composite, used = scoring.compute_composite({}, {"a": 1.0})
    assert composite == 0.0
    assert used == {}


def test_compute_composite_clips_to_unit_range():
    composite, _ = scoring.compute_composite({"a": 5.0}, {"a": 1.0})
    assert composite == 1.0
    composite, _ = scoring.compute_composite({"a": -5.0}, {"a": 1.0})
    assert composite == -1.0


def test_label_for_uses_horizon_specific_thresholds():
    # Day and week thresholds are intentionally different (see config.py) -
    # a composite that's Bullish for "week" may be Neutral for "day".
    mid = (config.DAY_BULLISH_THRESHOLD + config.WEEK_BULLISH_THRESHOLD) / 2
    assert scoring.label_for(config.DAY_BULLISH_THRESHOLD, "day") == "Bullish"
    assert scoring.label_for(config.DAY_BEARISH_THRESHOLD, "day") == "Bearish"
    assert scoring.label_for(mid, "week") in ("Bullish", "Neutral")
    assert scoring.label_for(0.0, "day") == "Neutral"
    assert scoring.label_for(0.0, "week") == "Neutral"
