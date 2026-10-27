"""Flakiness scoring, and the `flaky` and `suggest` commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from steadygate import baseline, flaky
from steadygate.cli import main
from steadygate.models import Result, Status
from steadygate.store import Store


def trials(case: str, *outcomes: int | None) -> list[Result]:
    return [
        Result(case, "passed", None, Status.ERROR, "timeout")
        if v is None
        else Result(case, "passed", float(v))
        for v in outcomes
    ]


def by_case(cases: list[flaky.CaseFlakiness]) -> dict[str, flaky.CaseFlakiness]:
    return {c.case: c for c in cases}


def test_classifies_flaky_failing_passing_and_broken() -> None:
    run = [
        *trials("steady", 1, 1, 1, 1),
        *trials("wobbly", 1, 0, 1, 0),
        *trials("dead", 0, 0, 0),
        *trials("crashing", None, None),
        Result("steady", "latency_ms", 900.0),
        Result("skipped", "passed", None, Status.SKIPPED),
    ]
    cases = flaky.analyze([run])
    got = by_case(cases)
    assert set(got) == {"steady", "wobbly", "dead", "crashing"}
    assert [c.status for c in cases] == ["flaky", "failing", "broken", "passing"]
    assert got["wobbly"].flips == 3
    assert got["wobbly"].flip_rate == 1.0
    assert got["wobbly"].pass_rate == 0.5
    assert got["crashing"].errors == 2
    assert got["crashing"].pass_rate is None
    assert got["steady"].flip_rate == 0.0


def test_errors_never_break_a_streak_or_count_as_failures() -> None:
    (case,) = flaky.analyze([trials("c", 1, None, 1, None, 1)])
    assert (case.status, case.trials, case.errors, case.flips) == ("passing", 3, 2, 0)


def test_trials_read_across_runs_in_order() -> None:
    (case,) = flaky.analyze([trials("c", 1, 1), trials("c", 0), trials("c", 0, 1)])
    assert (case.trials, case.passes, case.flips) == (5, 3, 2)


def test_flakiest_first() -> None:
    once = trials("once", 1, 1, 1, 0)  # 1 flip in 3 pairs
    often = trials("often", 1, 0, 1, 0)  # 3 flips in 3 pairs
    assert [c.case for c in flaky.analyze([once + often])] == ["often", "once"]


def test_interval_covers_the_pass_rate() -> None:
    (case,) = flaky.analyze([trials("c", *([1] * 7 + [0] * 3))])
    assert case.pass_rate_low is not None and case.pass_rate_high is not None
    assert case.pass_rate_low < 0.7 < case.pass_rate_high
    assert case.as_dict()["pass_rate_interval"] == [case.pass_rate_low, case.pass_rate_high]


def test_text_and_markdown_list_quarantine_candidates() -> None:
    cases = flaky.analyze([trials("ok", 1, 1) + trials("bad|case", 1, 0)])
    out = flaky.text(cases, "passed")
    assert "1 flaky, 1 passing" in out
    assert "Quarantine candidates:\n  - bad|case" in out
    assert "ok " not in out  # steady cases are summarised, not listed
    md = flaky.markdown(cases, "passed")
    assert "| `bad/case` | flaky | 1/2 |" in md


def test_markdown_when_nothing_is_flaky() -> None:
    md = flaky.markdown(flaky.analyze([trials("ok", 1, 1)]), "passed")
    assert "Every case gave the same outcome" in md


@pytest.mark.parametrize(
    ("metric", "direction"),
    [
        ("accuracy", "higher_is_better"),
        ("pass_rate", "higher_is_better"),
        ("is_safe", "higher_is_better"),
        ("latency_ms", "lower_is_better"),
        ("duration_s", "lower_is_better"),
        ("cost", "lower_is_better"),
        ("error_count", "lower_is_better"),
    ],
)
def test_guess_direction(metric: str, direction: str) -> None:
    assert baseline.guess_direction(metric) == direction


# --- CLI ------------------------------------------------------------------------------


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "steadygate.yml").write_text(
        "store: history.db\nmin_history: 3\nrules:\n"
        "  - type: relative_delta\n    metric: accuracy\n    max_regression: 0.05\n",
        encoding="utf-8",
    )
    return tmp_path


def cfg(project: Path) -> list[str]:
    return ["-c", str(project / "steadygate.yml")]


def test_flaky_cli_over_several_files(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = write_jsonl(project / "a.jsonl", [{"case": "x", "metric": "passed", "value": 1}])
    b = write_jsonl(project / "b.jsonl", [{"case": "x", "metric": "passed", "value": 0}])
    md = project / "flaky.md"
    assert main(["flaky", str(a), str(b), "--markdown", str(md)]) == 0
    assert "Quarantine candidates:\n  - x" in capsys.readouterr().out
    assert "| `x` | flaky |" in md.read_text(encoding="utf-8")


def test_flaky_cli_json(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = write_jsonl(project / "a.jsonl", [{"case": "x", "metric": "ok", "value": 1}])
    assert main(["flaky", str(a), "--metric", "ok", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["metric"] == "ok"
    assert out["cases"][0]["status"] == "passing"


def test_flaky_cli_reads_history(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with Store(project / "history.db") as store:
        for i, v in enumerate([1.0, 0.0, 1.0]):
            store.record(f"r{i}", [Result("x", "passed", v)])
    assert main(["flaky", "--history", *cfg(project)]) == 0
    assert "2/3" in capsys.readouterr().out


def test_flaky_cli_errors(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["flaky"]) == 3
    a = write_jsonl(project / "a.jsonl", [{"case": "x", "metric": "accuracy", "value": 1}])
    assert main(["flaky", str(a)]) == 3
    assert "no results for metric 'passed'" in capsys.readouterr().err
    assert main(["flaky", str(project / "missing.jsonl")]) == 3


def test_suggest_cli(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with Store(project / "history.db") as store:
        for i, acc in enumerate([0.80, 0.90, 0.85, 0.88]):
            store.record(
                f"r{i}", [Result("c", "accuracy", acc), Result("c", "latency_ms", 900.0 + i)]
            )
    assert main(["suggest", *cfg(project)]) == 0
    out = capsys.readouterr().out
    assert "# accuracy:" in out
    assert "metric: latency_ms\n  direction: lower_is_better" in out

    assert main(["suggest", "--metric", "accuracy", "--json", *cfg(project)]) == 0
    (s,) = json.loads(capsys.readouterr().out)
    assert s["suggested"]["direction"] == "higher_is_better"
    assert s["history_runs"] == 4


def test_suggest_cli_without_enough_history(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["suggest", *cfg(project)]) == 2
    assert "no history yet" in capsys.readouterr().err
    with Store(project / "history.db") as store:
        store.record("r0", [Result("c", "accuracy", 0.9)])
    assert main(["suggest", *cfg(project)]) == 2
    assert "not enough history (1/3 runs recorded)" in capsys.readouterr().out


def test_suggest_cli_bad_config(tmp_path: Path) -> None:
    assert main(["suggest", "-c", str(tmp_path / "nope.yml")]) == 3
