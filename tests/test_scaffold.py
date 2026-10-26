from __future__ import annotations

import pytest
import yaml

from steadygate import config
from steadygate.scaffold import (
    FORMATS,
    Answers,
    default_answers,
    plan_files,
    render_config,
    render_workflow,
    write_files,
)


@pytest.mark.parametrize("fmt", FORMATS)
def test_rendered_config_loads(tmp_path, fmt):
    p = tmp_path / "steadygate.yml"
    p.write_text(render_config(default_answers(fmt)), encoding="utf-8")
    cfg = config.load(p)
    assert cfg.window == 10 and cfg.min_history == 3
    assert cfg.rules[0].metric == default_answers(fmt).metric


def test_defaults_per_format():
    assert default_answers("promptfoo") == Answers(
        "promptfoo",
        "results.json",
        "score",
        0.05,
        "npx promptfoo eval --repeat 5 --output results.json",
    )
    assert default_answers("pytest-json").metric == "passed"
    assert default_answers("junit").results_path == "junit.xml"
    assert default_answers("jsonl") == Answers("jsonl", "results.jsonl", "accuracy", 0.05, "")


def test_config_has_fixed_settings_and_comment():
    text = render_config(default_answers("jsonl"))
    raw = yaml.safe_load(text)
    assert raw["store"] == ".steadygate/history.db"
    assert raw["rules"][0] == {
        "type": "relative_delta",
        "metric": "accuracy",
        "max_regression": 0.05,
    }
    assert "# The mean accuracy may not drop more than 5% below recent runs." in text


def test_pass_rate_min_is_one_minus_tolerance():
    raw = yaml.safe_load(render_config(Answers("junit", "junit.xml", "passed", 0.1)))
    assert raw["rules"][0] == {
        "type": "pass_rate",
        "metric": "passed",
        "min": 0.9,
        "scope": "case",
    }


def test_workflow_uses_action_ref_and_results():
    wf = yaml.safe_load(render_workflow(default_answers("promptfoo")))
    steps = wf["jobs"]["gate"]["steps"]
    gate = next(s for s in steps if "uses" in s and s["uses"].startswith("Mesmoraz/steadygate"))
    assert gate["uses"] == "Mesmoraz/steadygate@v1"
    assert gate["with"]["results"] == "results.json"
    assert any(s.get("run") == "npx promptfoo eval --repeat 5 --output results.json" for s in steps)


def test_workflow_without_eval_command_has_placeholder_comment():
    text = render_workflow(default_answers("jsonl"))
    assert "# TODO: run your evals here" in text
    yaml.safe_load(text)  # still valid


def test_eval_command_with_yaml_special_characters_stays_valid():
    a = Answers("jsonl", "r.jsonl", "accuracy", 0.05, "pytest -k 'a: b' # all")
    steps = yaml.safe_load(render_workflow(a))["jobs"]["gate"]["steps"]
    assert any(s.get("run") == "pytest -k 'a: b' # all" for s in steps)


def test_backslash_results_path_is_normalised():  # Review Focus 3
    a = Answers("jsonl", "out\\results.jsonl", "accuracy")
    assert "out/results.jsonl" in render_workflow(a)
    assert "\\" not in render_workflow(a)


def test_plan_marks_existing(tmp_path):
    (tmp_path / "steadygate.yml").write_text("old", encoding="utf-8")
    planned = plan_files(tmp_path, default_answers("jsonl"))
    assert [p.exists for p in planned] == [True, False]
    assert planned[1].path == tmp_path / ".github" / "workflows" / "steadygate.yml"


def test_write_never_overwrites_unless_listed(tmp_path):
    (tmp_path / "steadygate.yml").write_text("old", encoding="utf-8")
    planned = plan_files(tmp_path, default_answers("jsonl"))
    written = write_files(planned, overwrite=set())
    assert (tmp_path / "steadygate.yml").read_text(encoding="utf-8") == "old"
    assert written == [planned[1].path]
    assert write_files(planned, overwrite={planned[0].path}) == [p.path for p in planned]
    assert b"\r" not in planned[0].path.read_bytes()
