from __future__ import annotations

from steadygate import report
from steadygate.models import GateResult, Outcome, Result, RuleOutcome, Status, Verdict


def make(n_errors: int) -> GateResult:
    return GateResult(
        verdict=Verdict.INCONCLUSIVE,
        outcomes=[
            RuleOutcome("absolute:acc", "acc", True, Outcome.INCONCLUSIVE, "a | b"),
            RuleOutcome("relative_delta:lat", "lat", False, Outcome.NO_BASELINE, "0/3"),
        ],
        errors=[Result(f"c{i}", "acc", None, Status.ERROR, "timeout") for i in range(n_errors)],
        total_results=20,
    )


def test_text_truncates_long_error_lists() -> None:
    out = report.text(make(12))
    assert out.splitlines()[0] == "steadygate: INCONCLUSIVE"
    assert "(optional)" in out
    assert "... and 2 more" in out


def test_markdown_escapes_pipes_and_truncates() -> None:
    out = report.markdown(make(12))
    assert "a \\| b" in out
    assert "- ... and 2 more" in out
    assert "exit code 2" in out


def test_markdown_without_errors_has_no_details_block() -> None:
    assert "<details>" not in report.markdown(make(0))
