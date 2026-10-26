from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def test_pytest_json_maps_outcomes_durations_and_metadata(fixtures: Path) -> None:
    results = load_results(fixtures / "pytest-report.json")
    by = {(r.case.split("::")[1], r.metric): r for r in results}

    assert by["test_refund_policy", "passed"].value == 1.0
    assert by["test_refund_policy", "duration_s"].value == 1.5
    assert by["test_refund_policy", "accuracy"].value == 0.92
    assert by["test_refund_policy", "tokens"].value == 812.0
    assert ("test_refund_policy", "judge") not in by  # strings are not metrics
    assert ("test_refund_policy", "cached") not in by  # nor are booleans
    assert by["test_escalation", "passed"].value == 0.0
    assert by["test_escalation", "accuracy"].value == 0.4  # failed tests keep their scores
    assert by["test_lucky", "passed"].value == 1.0

    tone = by["test_judge_tone", "passed"]
    assert tone.status is Status.ERROR
    assert tone.message == "TimeoutError: judge API timed out"
    assert by["test_teardown_crash", "passed"].message == "RuntimeError: db still open"
    assert by["test_spanish", "passed"].status is Status.SKIPPED
    assert by["test_known_bug", "passed"].status is Status.SKIPPED


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('{"tests": [{"outcome": "passed"}]}', "nodeid"),
        ('{"tests": [{"nodeid": "t", "outcome": "rerun"}]}', "unknown outcome 'rerun'"),
    ],
)
def test_pytest_json_rejects_bad_entries(tmp_path: Path, body: str, message: str) -> None:
    p = tmp_path / "r.json"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(AdapterError, match=message):
        load_results(p)


def test_pytest_json_error_without_stage_details(tmp_path: Path) -> None:
    p = tmp_path / "r.json"
    p.write_text('{"tests": [{"nodeid": "t", "outcome": "error"}]}', encoding="utf-8")
    (r,) = load_results(p)
    assert (r.status, r.message) == (Status.ERROR, "")


def test_pytest_json_needs_tests_list(tmp_path: Path) -> None:
    p = tmp_path / "r.txt"
    p.write_text('{"summary": {}}', encoding="utf-8")
    with pytest.raises(AdapterError, match="'tests' list"):
        load_results(p, "pytest-json")


def test_promptfoo_maps_rows(fixtures: Path) -> None:
    results = load_results(fixtures / "promptfoo.json")
    refund = [r for r in results if r.case == "refund policy"]

    assert [r.value for r in refund if r.metric == "passed"] == [1.0, 0.0]  # two repeats
    assert [r.value for r in refund if r.metric == "score"] == [1.0, 0.25]
    assert [r.value for r in refund if r.metric == "latency_ms"] == [812.0, 640.0]
    assert [r.value for r in refund if r.metric == "cost"] == [0.0004]
    assert [r.value for r in refund if r.metric == "helpfulness"] == [0.9]
    assert not [r for r in refund if r.metric == "flag"]
    assert all(r.status is Status.OK for r in refund)  # an assertion failure is not an error

    (broken,) = [r for r in results if r.case == "test-1"]
    assert broken.status is Status.ERROR
    assert broken.message == "API error: 429 Too Many Requests"


def _row(desc: str, idx: int, provider: str, prompt: str) -> dict[str, Any]:
    return {
        "testIdx": idx,
        "testCase": {"description": desc},
        "provider": {"id": provider},
        "prompt": {"label": prompt},
        "success": True,
    }


def test_promptfoo_case_names_split_by_provider_prompt_and_duplicate_descriptions(
    tmp_path: Path,
) -> None:
    rows = [
        _row("greet", 0, "openai:a", "p1"),
        _row("greet", 0, "openai:b", "p2"),
        _row("same", 1, "openai:a", "p1"),
        _row("same", 2, "openai:a", "p1"),
    ]
    p = tmp_path / "out.json"
    p.write_text(json.dumps({"results": rows}), encoding="utf-8")  # older, flat layout
    cases = [r.case for r in load_results(p) if r.metric == "passed"]
    assert cases == [
        "greet [openai:a | p1]",
        "greet [openai:b | p2]",
        "same #1 [openai:a | p1]",
        "same #2 [openai:a | p1]",
    ]


def test_promptfoo_legacy_error_without_failure_reason(tmp_path: Path) -> None:
    rows = [
        {"testIdx": 0, "success": False, "error": "timeout", "provider": "x", "promptIdx": 0},
        {"testIdx": 1, "success": False, "error": "wrong", "gradingResult": {"pass": False}},
    ]
    p = tmp_path / "out.json"
    p.write_text(json.dumps({"results": {"results": rows}}), encoding="utf-8")
    first, second = load_results(p)
    assert first.status is Status.ERROR
    assert (second.status, second.value) == (Status.OK, 0.0)


def test_promptfoo_needs_results_list(tmp_path: Path) -> None:
    p = tmp_path / "out.txt"
    p.write_text('{"results": {"table": {}}}', encoding="utf-8")
    with pytest.raises(AdapterError, match="'results' list"):
        load_results(p, "promptfoo")


def test_json_detection_sniffs_contents(tmp_path: Path, fixtures: Path) -> None:
    assert detect_format(fixtures / "pytest-report.json") == "pytest-json"
    assert detect_format(fixtures / "promptfoo.json") == "promptfoo"
    assert detect_format(fixtures / "playwright.json") == "playwright"
    for body, message in (("[]", "which tool"), ("{nope", "as JSON")):
        p = tmp_path / "x.json"
        p.write_text(body, encoding="utf-8")
        with pytest.raises(AdapterError, match=message):
            detect_format(p)


def test_playwright_maps_tests_retries_and_broken_runs(fixtures: Path) -> None:
    results = load_results(fixtures / "playwright.json")
    by = {(r.case, r.metric): r for r in results}
    coupon = "checkout.spec.ts > cart > applies coupon"

    assert by["checkout.spec.ts > loads the page [chromium]", "passed"].value == 1.0
    assert by["checkout.spec.ts > loads the page [firefox]", "duration_s"].value == 2.1
    assert by[f"{coupon} [chromium]", "passed"].value == 1.0  # flaky: passed on retry
    assert by[f"{coupon} [chromium]", "retries"].value == 1.0
    assert by[f"{coupon} [chromium]", "duration_s"].value == 0.8  # the final attempt
    assert by[f"{coupon} [firefox]", "passed"].value == 0.0  # a timeout is a failure
    assert by[f"{coupon} [firefox]", "passed"].status is Status.OK
    assert by["checkout.spec.ts > cart > known bug [chromium]", "passed"].value == 1.0

    interrupted = by["checkout.spec.ts > cart > pays with card [chromium]", "passed"]
    never_ran = by["checkout.spec.ts > cart > pays with card [firefox]", "passed"]
    assert interrupted.status is Status.ERROR and "interrupted" in interrupted.message
    assert never_ran.status is Status.ERROR and "did not run" in never_ran.message
    for project in ("chromium", "firefox"):
        wrap = by[f"checkout.spec.ts > cart > gift wrap [{project}]", "passed"]
        assert wrap.status is Status.SKIPPED

    (setup,) = [r for r in results if r.case == "playwright run"]
    assert (setup.status, setup.message) == (
        Status.ERROR,
        "Error: globalSetup: database not reachable",
    )


def test_playwright_single_project_has_no_suffix(tmp_path: Path) -> None:
    report = {
        "suites": [
            {
                "title": "a.spec.ts",
                "specs": [
                    {
                        "title": "works",
                        "tests": [
                            {
                                "projectName": "",
                                "expectedStatus": "passed",
                                "results": [{"status": "passed"}],
                            },
                            {
                                "projectName": "",
                                "expectedStatus": "passed",
                                "results": [{"status": "failed"}],
                            },
                        ],
                    }
                ],
            }
        ]
    }
    p = tmp_path / "r.json"
    p.write_text(json.dumps(report), encoding="utf-8")
    passed = [(r.case, r.value) for r in load_results(p) if r.metric == "passed"]
    assert passed == [("a.spec.ts > works", 1.0), ("a.spec.ts > works", 0.0)]  # --repeat-each


def test_playwright_needs_suites_list(tmp_path: Path) -> None:
    p = tmp_path / "r.txt"
    p.write_text('{"config": {}}', encoding="utf-8")
    with pytest.raises(AdapterError, match="'suites' list"):
        load_results(p, "playwright")
