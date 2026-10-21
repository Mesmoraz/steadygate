from __future__ import annotations

import pytest

from steadygate.baseline import suggest


def test_not_enough_history() -> None:
    s = suggest("acc", [0.9, 0.91], "higher_is_better", min_history=3)
    assert s.rule is None
    assert s.rationale == "2/3 runs recorded"
    assert s.as_dict()["suggested"] is None


def test_flat_history_gets_floor() -> None:
    s = suggest("rss_mb", [400, 400, 400], "lower_is_better", 3)
    assert s.rule == {
        "type": "relative_delta",
        "metric": "rss_mb",
        "direction": "lower_is_better",
        "max_regression": 0.05,
    }
    assert s.history_runs == 3


def test_noisy_history_widens_limit() -> None:
    s = suggest("acc", [0.80, 0.90, 1.00, 0.85, 0.95], "higher_is_better", 3)
    # median 0.90, mad 0.05, noise 0.0556 -> 2x = 0.111 -> ceil 0.12
    assert s.noise == pytest.approx(0.05 / 0.90)
    assert s.rule is not None
    assert s.rule["max_regression"] == 0.12
    assert "noise" in s.rationale


def test_zero_median_noise_is_zero() -> None:
    assert suggest("errors", [0, 0, 1], "lower_is_better", 3).noise == 0.0


def test_bad_direction() -> None:
    with pytest.raises(ValueError):
        suggest("x", [1, 2, 3], "up", 3)
