"""mad_outlier and pass_rate: the rules that measure noise instead of ignoring it."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_rules import ctx, err, ok

from steadygate.models import Outcome
from steadygate.rules import MadOutlier, PassRate
from steadygate.stats import wilson

# --- wilson ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("successes", "n", "lo", "hi"),
    [
        (8, 10, 0.4902, 0.9433),  # textbook values for z = 1.96
        (0, 10, 0.0, 0.2775),
        (10, 10, 0.7225, 1.0),
        (45, 50, 0.7864, 0.9565),
    ],
)
def test_wilson_matches_reference_values(successes: int, n: int, lo: float, hi: float) -> None:
    got = wilson(successes, n, 0.95)
    assert got[0] == pytest.approx(lo, abs=1e-4)
    assert got[1] == pytest.approx(hi, abs=1e-4)


def test_wilson_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        wilson(1, 0, 0.95)
    with pytest.raises(ValueError):
        wilson(3, 2, 0.95)
    with pytest.raises(ValueError):
        wilson(1, 2, 1.0)


@given(st.integers(1, 500).flatmap(lambda n: st.tuples(st.integers(0, n), st.just(n))))
def test_wilson_contains_the_observed_rate(case: tuple[int, int]) -> None:
    successes, n = case
    lo, hi = wilson(successes, n, 0.95)
    assert 0.0 <= lo <= successes / n <= hi <= 1.0


@given(st.integers(0, 20), st.integers(1, 20), st.integers(2, 10))
def test_wilson_narrows_with_more_trials(successes: int, extra: int, scale: int) -> None:
    n = successes + extra
    lo, hi = wilson(successes, n, 0.95)
    lo2, hi2 = wilson(successes * scale, n * scale, 0.95)
    assert hi2 - lo2 < hi - lo


@given(st.integers(1, 200).flatmap(lambda n: st.tuples(st.integers(0, n), st.just(n))))
def test_wilson_widens_with_more_confidence(case: tuple[int, int]) -> None:
    successes, n = case
    lo90, hi90 = wilson(successes, n, 0.90)
    lo99, hi99 = wilson(successes, n, 0.99)
    assert lo99 <= lo90 and hi90 <= hi99


# --- mad_outlier ----------------------------------------------------------------------

# median 10, MAD 0.1, scaled MAD 0.14826
STEADY = [10.0, 10.2, 9.8, 10.1, 9.9]


def test_mad_outlier_passes_normal_noise() -> None:
    rule = MadOutlier("acc", k=3.0)
    out = rule.evaluate([ok("a", 9.7)], ctx(STEADY))  # -2.0 scaled MADs
    assert out.outcome is Outcome.PASS
    assert out.baseline == pytest.approx(10.0)
    assert "-2.0 MADs" in out.detail


def test_mad_outlier_fails_a_real_drop() -> None:
    out = MadOutlier("acc", k=3.0).evaluate([ok("a", 9.4)], ctx(STEADY))  # -4.0 MADs
    assert out.outcome is Outcome.FAIL
    assert out.observed == pytest.approx(9.4)


def test_mad_outlier_only_fails_in_the_worse_direction() -> None:
    better = MadOutlier("acc", k=3.0).evaluate([ok("a", 11.0)], ctx(STEADY))
    assert better.outcome is Outcome.PASS
    lower = MadOutlier("ms", k=3.0, direction="lower_is_better")
    assert lower.evaluate([ok("a", 11.0, "ms")], ctx(STEADY)).outcome is Outcome.FAIL
    assert lower.evaluate([ok("a", 9.0, "ms")], ctx(STEADY)).outcome is Outcome.PASS


def test_mad_outlier_two_sided() -> None:
    rule = MadOutlier("acc", k=3.0, direction="both")
    assert rule.evaluate([ok("a", 11.0)], ctx(STEADY)).outcome is Outcome.FAIL
    assert rule.evaluate([ok("a", 9.0)], ctx(STEADY)).outcome is Outcome.FAIL
    assert rule.evaluate([ok("a", 10.1)], ctx(STEADY)).outcome is Outcome.PASS


def test_mad_outlier_without_spread_fails_any_worse_value() -> None:
    rule = MadOutlier("acc", k=3.0)
    assert rule.evaluate([ok("a", 5.0)], ctx([5.0, 5.0, 5.0])).outcome is Outcome.PASS
    out = rule.evaluate([ok("a", 4.9)], ctx([5.0, 5.0, 5.0]))
    assert out.outcome is Outcome.FAIL
    assert "no spread" in out.detail


def test_mad_outlier_waits_for_history() -> None:
    out = MadOutlier("acc").evaluate([ok("a", 1.0)], ctx([1.0, 1.0]))
    assert out.outcome is Outcome.NO_BASELINE


def test_mad_outlier_errors_block_a_pass() -> None:
    out = MadOutlier("acc").evaluate([ok("a", 10.0), err("b")], ctx(STEADY))
    assert out.outcome is Outcome.INCONCLUSIVE
    assert MadOutlier("acc").evaluate([err("b")], ctx(STEADY)).outcome is Outcome.INCONCLUSIVE


def test_mad_outlier_from_spec() -> None:
    rule = MadOutlier.from_spec(
        {"metric": "ms", "k": 4, "direction": "lower_is_better", "required": False}
    )
    assert rule == MadOutlier("ms", 4.0, direction="lower_is_better", required=False)
    assert MadOutlier.from_spec({"metric": "acc"}).k == 3.0
    assert MadOutlier.from_spec({"metric": "acc", "direction": "both"}).direction == "both"


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"metric": "a", "k": 0}, "'k' must be a number > 0"),
        ({"metric": "a", "k": "3"}, "'k' must be a number"),
        ({"metric": "a", "direction": "up"}, "'direction'"),
    ],
)
def test_mad_outlier_bad_spec(spec: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        MadOutlier.from_spec(spec)


# --- pass_rate ------------------------------------------------------------------------


def trials(case: str, passed: int, failed: int, metric: str = "passed") -> list:
    return [ok(case, 1.0, metric)] * passed + [ok(case, 0.0, metric)] * failed


def test_pass_rate_noise_does_not_fail_the_build() -> None:
    # 7/10 against a 0.8 target: the interval (0.40, 0.89) still reaches 0.8.
    rule = PassRate("passed", minimum=0.8)
    out = rule.evaluate(trials("t", 7, 3), ctx())
    assert out.outcome is Outcome.PASS
    assert out.observed == pytest.approx(0.7)
    assert "7/10" in out.detail and "can't rule out" in out.detail


def test_pass_rate_confident_pass_says_so() -> None:
    out = PassRate("passed", minimum=0.5).evaluate(trials("t", 45, 5), ctx())
    assert out.outcome is Outcome.PASS
    assert "can't rule out" not in out.detail


def test_pass_rate_fails_when_confidently_below() -> None:
    out = PassRate("passed", minimum=0.8).evaluate(trials("t", 3, 7), ctx())
    assert out.outcome is Outcome.FAIL
    assert "3/10" in out.detail


def test_pass_rate_case_scope_names_the_bad_case() -> None:
    rule = PassRate("passed", minimum=0.8, scope="case")
    results = trials("steady", 5, 0) + trials("broken", 0, 5) + trials("wobbly", 4, 1)
    out = rule.evaluate(results, ctx())
    assert out.outcome is Outcome.FAIL
    assert "1/3 case(s)" in out.detail and "broken" in out.detail
    good = trials("steady", 5, 0) + trials("wobbly", 4, 1)
    assert rule.evaluate(good, ctx()).outcome is Outcome.PASS


def test_pass_rate_needs_pass_fail_values() -> None:
    out = PassRate("passed", minimum=0.8).evaluate([ok("t", 0.5, "passed")], ctx())
    assert out.outcome is Outcome.INCONCLUSIVE
    assert "0 or 1" in out.detail


def test_pass_rate_errors_block_a_pass() -> None:
    results = [*trials("t", 10, 0), err("t", "passed")]
    assert PassRate("passed", minimum=0.5).evaluate(results, ctx()).outcome is Outcome.INCONCLUSIVE
    assert PassRate("passed", minimum=0.5).evaluate([], ctx()).outcome is Outcome.INCONCLUSIVE


def test_pass_rate_from_spec() -> None:
    rule = PassRate.from_spec({"metric": "passed", "min": 0.9, "confidence": 0.99, "scope": "case"})
    assert rule == PassRate("passed", 0.9, 0.99, "case")
    assert PassRate.from_spec({"metric": "passed", "min": 1}).confidence == 0.95


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"metric": "p"}, "'min' must be a number between 0 and 1"),
        ({"metric": "p", "min": 1.5}, "'min' must be a number between 0 and 1"),
        ({"metric": "p", "min": 0.8, "confidence": 1}, "'confidence'"),
        ({"metric": "p", "min": 0.8, "scope": "run"}, "'scope'"),
    ],
)
def test_pass_rate_bad_spec(spec: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        PassRate.from_spec(spec)
