from __future__ import annotations

import pytest

from steadygate.models import Outcome, Result, Status
from steadygate.rules import Absolute, Context, RelativeDelta


class FakeHistory:
    def __init__(self, values: list[float], cases: list[set[str]] | None = None) -> None:
        self.values = values
        self.cases = cases or []
        self.calls: list[tuple[str, int, str | None]] = []
        self.case_calls: list[tuple[int, str | None]] = []

    def run_aggregates(
        self, metric: str, window: int, exclude_run: str | None = None
    ) -> list[float]:
        self.calls.append((metric, window, exclude_run))
        return self.values[:window]

    def recent_cases(self, window: int, exclude_run: str | None = None) -> list[set[str]]:
        self.case_calls.append((window, exclude_run))
        return self.cases[:window]


def ctx(history: list[float] | None = None, min_history: int = 3) -> Context:
    return Context(FakeHistory(history or []), window=10, min_history=min_history, run_id="cand")


def ok(case: str, value: float, metric: str = "acc") -> Result:
    return Result(case, metric, value)


def err(case: str, metric: str = "acc") -> Result:
    return Result(case, metric, None, Status.ERROR, "boom")


# --- absolute -------------------------------------------------------------------------


def test_absolute_aggregate_pass_and_fail() -> None:
    rule = Absolute("acc", minimum=0.8, maximum=None)
    assert rule.evaluate([ok("a", 0.9), ok("b", 0.8)], ctx()).outcome is Outcome.PASS
    out = rule.evaluate([ok("a", 0.9), ok("b", 0.6)], ctx())
    assert out.outcome is Outcome.FAIL
    assert out.observed == pytest.approx(0.75)
    assert "is not >= 0.8" in out.detail


def test_absolute_case_scope_names_failing_cases() -> None:
    rule = Absolute("passed", minimum=1.0, maximum=None, scope="case")
    results = [ok(f"t{i}", 0.0, "passed") for i in range(7)] + [ok("good", 1.0, "passed")]
    out = rule.evaluate(results, ctx())
    assert out.outcome is Outcome.FAIL
    assert out.detail.startswith("7/8 case(s)")
    assert out.detail.endswith("...")  # only the first five are listed


def test_absolute_max_bound() -> None:
    rule = Absolute("latency", minimum=None, maximum=1000)
    assert rule.evaluate([ok("a", 1200, "latency")], ctx()).outcome is Outcome.FAIL
    assert rule.evaluate([ok("a", 900, "latency")], ctx()).outcome is Outcome.PASS


def test_errors_never_turn_into_a_pass() -> None:
    rule = Absolute("acc", minimum=0.5, maximum=None)
    out = rule.evaluate([ok("a", 0.9), err("b")], ctx())
    assert out.outcome is Outcome.INCONCLUSIVE
    assert "1 result(s) errored" in out.detail


def test_errors_do_not_hide_a_real_failure() -> None:
    rule = Absolute("acc", minimum=0.5, maximum=None)
    assert rule.evaluate([ok("a", 0.1), err("b")], ctx()).outcome is Outcome.FAIL


def test_no_usable_data_is_inconclusive() -> None:
    rule = Absolute("acc", minimum=0.5, maximum=None)
    assert rule.evaluate([err("a"), ok("x", 1.0, "other")], ctx()).outcome is Outcome.INCONCLUSIVE
    assert rule.evaluate([], ctx()).outcome is Outcome.INCONCLUSIVE


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"metric": "m"}, "needs 'min', 'max'"),
        ({"metric": "m", "min": 2, "max": 1}, "greater than"),
        ({"metric": "m", "min": "high"}, "must be a number"),
        ({"metric": "m", "min": True}, "must be a number"),
        ({"metric": "m", "min": 1, "scope": "suite"}, "scope"),
        ({"metric": "m", "min": 1, "required": "yes"}, "required"),
    ],
)
def test_absolute_spec_validation(spec: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Absolute.from_spec(spec)


# --- relative_delta -------------------------------------------------------------------


def test_relative_waits_for_enough_history() -> None:
    rule = RelativeDelta("acc", max_regression=0.05)
    out = rule.evaluate([ok("a", 0.1)], ctx([0.9, 0.9]))
    assert out.outcome is Outcome.NO_BASELINE
    assert "2/3" in out.detail


def test_relative_uses_median_so_one_outlier_run_does_not_move_baseline() -> None:
    rule = RelativeDelta("acc", max_regression=0.05)
    history = [0.90, 0.91, 0.89, 0.20]  # one broken past run
    out = rule.evaluate([ok("a", 0.87)], ctx(history))
    assert out.baseline == pytest.approx(0.895)
    assert out.outcome is Outcome.PASS  # 0.87 is 2.8% below 0.895


def test_relative_higher_is_better_fails_on_drop() -> None:
    rule = RelativeDelta("acc", max_regression=0.05)
    out = rule.evaluate([ok("a", 0.80)], ctx([0.90, 0.90, 0.90]))
    assert out.outcome is Outcome.FAIL
    assert "-11.1%" in out.detail


def test_relative_improvement_always_passes() -> None:
    rule = RelativeDelta("acc", max_regression=0.0)
    assert rule.evaluate([ok("a", 0.99)], ctx([0.9, 0.9, 0.9])).outcome is Outcome.PASS


def test_relative_lower_is_better() -> None:
    rule = RelativeDelta("lat", max_regression=0.2, higher_is_better=False)
    hist = [1000.0, 1000.0, 1000.0]
    assert rule.evaluate([ok("a", 1150, "lat")], ctx(hist)).outcome is Outcome.PASS
    assert rule.evaluate([ok("a", 1300, "lat")], ctx(hist)).outcome is Outcome.FAIL
    assert rule.evaluate([ok("a", 500, "lat")], ctx(hist)).outcome is Outcome.PASS


def test_relative_zero_baseline() -> None:
    rule = RelativeDelta("errors", max_regression=0.1, higher_is_better=False)
    assert rule.evaluate([ok("a", 0, "errors")], ctx([0, 0, 0])).outcome is Outcome.PASS
    assert rule.evaluate([ok("a", 1, "errors")], ctx([0, 0, 0])).outcome is Outcome.FAIL


def test_relative_excludes_candidate_run_from_history() -> None:
    c = ctx([0.9, 0.9, 0.9])
    RelativeDelta("acc", max_regression=0.05).evaluate([ok("a", 0.9)], c)
    assert c.history.calls == [("acc", 10, "cand")]  # type: ignore[attr-defined]


def test_relative_errors_and_missing_data() -> None:
    rule = RelativeDelta("acc", max_regression=0.05)
    hist = [0.9, 0.9, 0.9]
    assert rule.evaluate([ok("a", 0.9), err("b")], ctx(hist)).outcome is Outcome.INCONCLUSIVE
    assert rule.evaluate([err("b")], ctx(hist)).outcome is Outcome.INCONCLUSIVE


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"metric": "m"}, "max_regression"),
        ({"metric": "m", "max_regression": -0.1}, "max_regression"),
        ({"metric": "m", "max_regression": 0.1, "direction": "up"}, "direction"),
    ],
)
def test_relative_spec_validation(spec: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        RelativeDelta.from_spec(spec)


def test_relative_from_spec_direction() -> None:
    rule = RelativeDelta.from_spec(
        {"metric": "lat", "max_regression": 0.2, "direction": "lower_is_better", "required": False}
    )
    assert rule == RelativeDelta("lat", 0.2, higher_is_better=False, required=False)
    assert rule.name == "relative_delta:lat"
