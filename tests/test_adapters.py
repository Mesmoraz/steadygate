from __future__ import annotations

from pathlib import Path

import pytest

from steadygate.adapters import AdapterError, detect_format, load_results
from steadygate.models import Status


def test_junit_maps_pass_fail_error_skip(fixtures: Path) -> None:
    results = load_results(fixtures / "pytest.xml")
    passed = {r.case.split("::")[1]: r for r in results if r.metric == "passed"}

    assert passed["test_refund_policy"].value == 1.0
    assert passed["test_escalation"].value == 0.0
    assert passed["test_judge_tone"].status is Status.ERROR
    assert passed["test_judge_tone"].message == "judge API timed out after 30s"
    assert passed["test_spanish"].status is Status.SKIPPED

    durations = [r for r in results if r.metric == "duration_s"]
    assert len(durations) == 3  # pass + pass + failure; error and skip have no duration
    assert passed["test_refund_policy"].case == "tests.test_agent::test_refund_policy"


def test_junit_case_without_classname(tmp_path: Path) -> None:
    p = tmp_path / "r.xml"
    p.write_text('<testsuite><testcase name="solo"/></testsuite>', encoding="utf-8")
    (r,) = load_results(p)
    assert r.case == "solo"


def test_jsonl_reads_statuses_and_skips_blank_lines(fixtures: Path) -> None:
    results = load_results(fixtures / "evals.jsonl")
    assert len(results) == 6
    errored = [r for r in results if r.status is Status.ERROR]
    assert [(r.case, r.message) for r in errored] == [("tone-check", "judge timeout")]
    assert next(r for r in results if r.case == "spanish").status is Status.SKIPPED


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ('{"metric": "a", "value": 1}', "missing field 'case'"),
        ('{"case": "x", "metric": "a"}', "'value' is required"),
        ("[1, 2]", "expected an object"),
        ('{"case": "x", "metric": "a", "value": 1, "status": "weird"}', "weird"),
        ("not json", "could not parse"),
    ],
)
def test_jsonl_rejects_bad_lines(tmp_path: Path, line: str, message: str) -> None:
    p = tmp_path / "r.jsonl"
    p.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(AdapterError, match=message):
        load_results(p)


def test_format_detection(tmp_path: Path) -> None:
    assert detect_format(Path("a.xml")) == "junit"
    assert detect_format(Path("a.ndjson")) == "jsonl"
    with pytest.raises(AdapterError, match="--format"):
        detect_format(Path("a.txt"))


def test_explicit_format_and_errors(tmp_path: Path, fixtures: Path) -> None:
    p = tmp_path / "results.txt"
    p.write_text('{"case": "a", "metric": "m", "value": 1}\n', encoding="utf-8")
    assert load_results(p, "jsonl")[0].value == 1.0
    with pytest.raises(AdapterError, match="unknown format"):
        load_results(p, "csv")
    with pytest.raises(AdapterError, match="not found"):
        load_results(tmp_path / "missing.jsonl")
