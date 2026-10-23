"""The expected-case manifest: a case that never reported is INCONCLUSIVE, never a pass."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_rules import FakeHistory

from steadygate import config, gate
from steadygate.models import Outcome, Result, Status, Verdict

RULES = [
    {"type": "absolute", "metric": "acc", "min": 0.5},
    {"type": "absolute", "metric": "latency", "max": 900, "required": False},
]


def cfg(expected: object = None, base: Path = Path()) -> config.Config:
    raw: dict[str, object] = {"rules": RULES}
    if expected is not None:
        raw["expected_cases"] = expected
    return config.parse(raw, base_dir=base)


def run(c: config.Config, results: list[Result]) -> gate.GateResult:
    return gate.evaluate(c, results, FakeHistory([]))


def full(case: str) -> list[Result]:
    return [Result(case, "acc", 0.9), Result(case, "latency", 100.0)]


def test_no_manifest_changes_nothing() -> None:
    result = run(cfg(), full("a"))
    assert result.verdict is Verdict.PASS
    assert [o.rule for o in result.outcomes] == ["absolute:acc", "absolute:latency"]


def test_all_expected_cases_present_passes() -> None:
    result = run(cfg(["a", "b"]), full("a") + full("b"))
    assert result.verdict is Verdict.PASS
    cov = result.outcomes[0]
    assert cov.rule == "coverage" and cov.outcome is Outcome.PASS and cov.required
    assert "2/2" in cov.detail


def test_missing_case_is_inconclusive() -> None:
    result = run(cfg(["a", "b", "c"]), full("a"))
    assert result.verdict is Verdict.INCONCLUSIVE
    cov = result.outcomes[0]
    assert cov.outcome is Outcome.INCONCLUSIVE
    assert "2/3 expected case(s) missing: b, c" in cov.detail


def test_dropped_metric_is_inconclusive() -> None:
    # Case b ran but never reported the gated metric: silent green if we ignored it.
    results = [*full("a"), Result("b", "latency", 100.0)]
    cov = run(cfg(["a", "b"]), results).outcomes[0]
    assert cov.outcome is Outcome.INCONCLUSIVE
    assert "b [acc]" in cov.detail


def test_optional_rule_metrics_are_not_required() -> None:
    results = [Result("a", "acc", 0.9)]  # no latency, which only an optional rule reads
    assert run(cfg(["a"]), results).verdict is Verdict.PASS


def test_errored_and_skipped_cases_count_as_reported() -> None:
    results = [
        Result("a", "acc", 0.9),
        Result("b", "acc", None, Status.SKIPPED),
        Result("c", "acc", None, Status.ERROR, "timeout"),
    ]
    out = run(cfg(["a", "b", "c"]), results)
    assert out.outcomes[0].outcome is Outcome.PASS  # the error is the rules' business
    assert out.verdict is Verdict.INCONCLUSIVE


def test_a_regression_still_wins() -> None:
    results = [Result("a", "acc", 0.1)]
    assert run(cfg(["a", "b"]), results).verdict is Verdict.REGRESSION


def test_long_missing_list_is_shortened() -> None:
    names = [f"case{i}" for i in range(12)]
    cov = run(cfg(names), full("case0")).outcomes[0]
    assert "11/12" in cov.detail and cov.detail.endswith("...")


def test_manifest_from_file(tmp_path: Path) -> None:
    (tmp_path / "cases.txt").write_text("# eval cases\na\n\n b \n", encoding="utf-8")
    c = cfg("cases.txt", base=tmp_path)
    assert c.expected_cases == ["a", "b"]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], "'expected_cases' must"),
        (["a", 3], "'expected_cases' must"),
        (["a", "a"], "listed twice: a"),
        (42, "'expected_cases' must"),
        ("missing.txt", "expected_cases file not found"),
    ],
)
def test_bad_manifest(tmp_path: Path, value: object, message: str) -> None:
    with pytest.raises(config.ConfigError, match=message):
        cfg(value, base=tmp_path)
