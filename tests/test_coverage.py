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


# --- no manifest: infer the expected cases from the baseline ---------------------------

BASELINE = [{"a", "b", "c"}, {"a", "b", "c"}, {"a", "b", "c", "d"}]


def run_with(cases: list[set[str]], results: list[Result]) -> gate.GateResult:
    return gate.evaluate(cfg(), results, FakeHistory([], cases), run_id="cand")


def test_case_every_baseline_run_reported_is_inferred() -> None:
    result = run_with(BASELINE, full("a") + full("b"))
    assert result.verdict is Verdict.INCONCLUSIVE
    cov = result.outcomes[0]
    assert cov.rule == "coverage" and cov.required and cov.outcome is Outcome.INCONCLUSIVE
    assert "1/3 baseline case(s) missing: c" in cov.detail


def test_inferred_cases_all_present_passes_and_says_so() -> None:
    # d showed up in only one baseline run, so its absence proves nothing.
    result = run_with(BASELINE, full("a") + full("b") + full("c"))
    assert result.verdict is Verdict.PASS
    cov = result.outcomes[0]
    assert cov.outcome is Outcome.PASS
    assert cov.detail == "all 3/3 case(s) from the last 3 baseline run(s) reported"


def test_inferred_cases_ignore_metrics() -> None:
    # The inferred check is per case; a case that reports anything counts as present.
    results = [*full("a"), *full("b"), Result("c", "latency", 100.0)]
    assert run_with(BASELINE, results).outcomes[0].outcome is Outcome.PASS


def test_errored_case_counts_as_reported_when_inferred() -> None:
    results = [*full("a"), *full("b"), Result("c", "acc", None, Status.ERROR, "timeout")]
    out = run_with(BASELINE, results)
    assert out.outcomes[0].outcome is Outcome.PASS
    assert out.verdict is Verdict.INCONCLUSIVE  # from the rule, not from coverage


def test_no_inference_before_min_history() -> None:
    result = run_with(BASELINE[:2], full("a"))
    assert [o.rule for o in result.outcomes] == ["absolute:acc", "absolute:latency"]


def test_no_inference_when_baseline_runs_share_no_case() -> None:
    result = run_with([{"a"}, {"b"}, {"c"}], full("z"))
    assert [o.rule for o in result.outcomes] == ["absolute:acc", "absolute:latency"]


def test_inference_reads_the_window_and_skips_the_candidate_run() -> None:
    history = FakeHistory([], BASELINE)
    gate.evaluate(cfg(), full("a"), history, run_id="cand")
    assert history.case_calls == [(10, "cand")]


def test_manifest_wins_over_inference() -> None:
    history = FakeHistory([], BASELINE)
    result = gate.evaluate(cfg(["a"]), full("a"), history)
    assert result.verdict is Verdict.PASS
    assert history.case_calls == []


# --- require_history: an empty history is not a pass ------------------------------------


def test_require_history_with_no_runs_is_inconclusive() -> None:
    result = gate.evaluate(cfg(), full("a"), FakeHistory([]), require_history=True)
    assert result.verdict is Verdict.INCONCLUSIVE
    hist = result.outcomes[0]
    assert hist.rule == "history" and hist.required and hist.outcome is Outcome.INCONCLUSIVE
    assert "no baseline runs" in hist.detail


def test_require_history_is_satisfied_by_any_run() -> None:
    result = gate.evaluate(cfg(), full("a"), FakeHistory([], [{"a"}]), require_history=True)
    assert result.verdict is Verdict.PASS
    assert "history" not in [o.rule for o in result.outcomes]


def test_empty_history_passes_by_default() -> None:
    assert gate.evaluate(cfg(), full("a"), FakeHistory([])).verdict is Verdict.PASS


def test_require_history_skips_gates_that_stand_on_their_own() -> None:
    # A case list and history-free rules (absolute here) can judge a run with no baseline.
    result = gate.evaluate(cfg(["a"]), full("a"), FakeHistory([]), require_history=True)
    assert result.verdict is Verdict.PASS
    assert "history" not in [o.rule for o in result.outcomes]


def test_require_history_applies_when_a_required_rule_needs_a_baseline() -> None:
    c = config.parse(
        {
            "expected_cases": ["a"],
            "rules": [{"type": "relative_delta", "metric": "acc", "max_regression": 0.05}],
        }
    )
    result = gate.evaluate(c, full("a"), FakeHistory([]), require_history=True)
    assert result.verdict is Verdict.INCONCLUSIVE
    assert result.outcomes[0].rule == "history"


def test_require_history_ignores_optional_rules_without_a_baseline() -> None:
    c = config.parse(
        {
            "expected_cases": ["a"],
            "rules": [
                {"type": "absolute", "metric": "acc", "min": 0.5},
                {
                    "type": "relative_delta",
                    "metric": "latency",
                    "max_regression": 0.25,
                    "required": False,
                },
            ],
        }
    )
    result = gate.evaluate(c, full("a"), FakeHistory([]), require_history=True)
    assert result.verdict is Verdict.PASS
