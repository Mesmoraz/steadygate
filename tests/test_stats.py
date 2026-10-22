from __future__ import annotations

import pytest

from steadygate.stats import summarize


def test_known_values() -> None:
    s = summarize([1, 2, 3, 4, 100])
    assert (s.n, s.mean, s.median, s.mad, s.min, s.max) == (5, 22.0, 3.0, 1.0, 1.0, 100.0)
    assert s.p25 == 2.0
    assert s.p75 == 4.0
    assert s.p95 == pytest.approx(80.8)


def test_even_n_median() -> None:
    assert summarize([1, 2, 3, 4]).median == 2.5


def test_single_value() -> None:
    s = summarize([7.0])
    assert (s.n, s.mean, s.median, s.mad, s.stdev, s.p95) == (1, 7.0, 7.0, 0.0, 0.0, 7.0)


def test_constant_values_have_zero_mad() -> None:
    assert summarize([5, 5, 5]).mad == 0.0


def test_empty_raises() -> None:
    with pytest.raises(ValueError, match="no values"):
        summarize([])


def test_as_dict_keys() -> None:
    assert list(summarize([1, 2]).as_dict()) == [
        "n",
        "mean",
        "median",
        "mad",
        "stdev",
        "min",
        "p25",
        "p75",
        "p95",
        "max",
    ]
