"""End-to-end tests through the CLI: the exit code is the product's contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from steadygate.cli import main

CONFIG = """\
store: history.db
min_history: 3
rules:
  - type: relative_delta
    metric: accuracy
    max_regression: 0.05
  - type: relative_delta
    metric: latency_ms
    direction: lower_is_better
    max_regression: 0.25
    required: false
"""


def write_results(path: Path, accuracy: list[float | None], latency: float = 900) -> Path:
    lines = []
    for i, acc in enumerate(accuracy):
        if acc is None:
            lines.append(json.dumps({"case": f"c{i}", "metric": "accuracy", "status": "error"}))
        else:
            lines.append(json.dumps({"case": f"c{i}", "metric": "accuracy", "value": acc}))
        lines.append(json.dumps({"case": f"c{i}", "metric": "latency_ms", "value": latency}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "steadygate.yml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


def run(project: Path, *args: str) -> int:
    return main([*args, "-c", str(project / "steadygate.yml")])


def seed_baseline(project: Path) -> None:
    for i, acc in enumerate([0.90, 0.92, 0.88]):
        res = write_results(project / f"base{i}.jsonl", [acc, acc])
        assert run(project, "record", str(res), "--run-id", f"base{i}") == 0


def test_first_runs_pass_without_baseline(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    res = write_results(project / "r.jsonl", [0.1, 0.1])
    assert run(project, "run", str(res)) == 0
    assert "NO_BASELINE" in capsys.readouterr().out


def test_require_history_refuses_to_pass_on_an_empty_store(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    res = write_results(project / "r.jsonl", [0.9, 0.9])
    md = project / "report.md"
    assert run(project, "run", str(res), "--require-history", "--markdown", str(md)) == 2
    assert "[INCONCLUSIVE] history: no baseline runs" in capsys.readouterr().out
    assert "`history`" in md.read_text(encoding="utf-8")


def test_require_history_gates_normally_once_runs_exist(project: Path) -> None:
    seed_baseline(project)
    res = write_results(project / "r.jsonl", [0.9, 0.9])
    assert run(project, "run", str(res), "--require-history") == 0


def test_pass_regression_and_inconclusive(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_baseline(project)

    assert run(project, "run", str(write_results(project / "ok.jsonl", [0.89, 0.91]))) == 0
    assert run(project, "run", str(write_results(project / "bad.jsonl", [0.70, 0.75]))) == 1
    broken = write_results(project / "broken.jsonl", [0.95, None])
    assert run(project, "run", str(broken)) == 2

    out = capsys.readouterr().out
    assert "steadygate: REGRESSION" in out
    assert "steadygate: INCONCLUSIVE" in out
    assert "1 result(s) errored" in out


def test_optional_rule_does_not_change_verdict(project: Path) -> None:
    seed_baseline(project)
    slow = write_results(project / "slow.jsonl", [0.9, 0.9], latency=5000)
    assert run(project, "run", str(slow)) == 0


def test_record_flag_only_records_passing_runs(project: Path) -> None:
    seed_baseline(project)
    bad = write_results(project / "bad.jsonl", [0.1, 0.1])
    assert run(project, "run", str(bad), "--record", "--run-id", "bad") == 1
    good = write_results(project / "good.jsonl", [0.9, 0.9])
    assert run(project, "run", str(good), "--record", "--run-id", "good") == 0

    from steadygate.store import Store

    with Store(project / "history.db") as s:
        assert s.run_count() == 4  # three seeds + good; bad was not recorded


def test_markdown_and_json_reports(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed_baseline(project)
    capsys.readouterr()
    res = write_results(project / "bad.jsonl", [0.5, None])
    md = project / "report.md"
    assert run(project, "run", str(res), "--json", "--markdown", str(md)) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "regression"
    assert payload["exit_code"] == 1
    assert payload["rules"][0]["rule"] == "coverage"  # inferred from the baseline
    assert payload["rules"][1]["outcome"] == "fail"
    assert payload["errors"] == [{"case": "c1", "metric": "accuracy", "message": ""}]

    text = md.read_text(encoding="utf-8")
    assert "Steadygate: REGRESSION" in text
    assert "`relative_delta:accuracy`" in text
    assert "1 result(s) errored" in text


def test_config_and_input_errors_exit_3(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(["run", str(tmp_path / "missing.jsonl"), "-c", str(project / "steadygate.yml")]) == 3
    )
    res = write_results(project / "r.jsonl", [0.9])
    assert main(["run", str(res), "-c", str(tmp_path / "nope.yml")]) == 3
    assert "config not found" in capsys.readouterr().err


def test_run_id_defaults_to_github_sha(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    res = write_results(project / "r.jsonl", [0.9])
    assert run(project, "record", str(res)) == 0
    assert "as run abc123" in capsys.readouterr().out


def test_junit_end_to_end(project: Path, fixtures: Path) -> None:
    (project / "junit.yml").write_text(
        "rules:\n  - type: absolute\n    metric: passed\n    min: 1\n    scope: case\n",
        encoding="utf-8",
    )
    assert main(["run", str(fixtures / "pytest.xml"), "-c", str(project / "junit.yml")]) == 1


def test_module_entry_point() -> None:
    import runpy

    with pytest.raises(SystemExit) as exc:
        import sys

        sys.argv = ["steadygate", "--version"]
        runpy.run_module("steadygate", run_name="__main__")
    assert exc.value.code == 0


def test_case_dropped_from_the_baseline_is_inconclusive(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # No expected_cases in the config: the baseline's own cases stand in for the list.
    seed_baseline(project)
    res = write_results(project / "r.jsonl", [0.91])  # c1 never reported
    assert run(project, "run", str(res)) == 2
    assert "1/2 baseline case(s) missing: c1" in capsys.readouterr().out
