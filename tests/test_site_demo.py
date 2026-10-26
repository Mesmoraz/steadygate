from __future__ import annotations

import dataclasses
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from steadygate.adapters import load_results

ROOT = Path(__file__).resolve().parent.parent
BASIC = ROOT / "examples" / "basic"
CONFIG = (BASIC / "steadygate.yml").read_text(encoding="utf-8")


def rows(name: str) -> list[dict[str, Any]]:
    return [{**dataclasses.asdict(r), "status": str(r.status)} for r in load_results(BASIC / name)]


BASELINES = [rows(f"baseline-{n}.jsonl") for n in (1, 2, 3)]


@pytest.fixture(scope="module")
def demo() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sg_demo", ROOT / "site" / "demo.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_matches_examples(demo: ModuleType) -> None:
    out = demo.run(rows("candidate-noise.jsonl"), CONFIG, BASELINES)
    assert out["report"]["verdict"] == "pass"
    assert out["markdown"].startswith("### ✅ Steadygate: PASS")
    regression = demo.run(rows("candidate-regression.jsonl"), CONFIG, BASELINES)
    assert regression["report"]["exit_code"] == 1


def test_run_with_no_results_is_inconclusive(demo: ModuleType) -> None:
    assert demo.run([], CONFIG, BASELINES)["report"]["verdict"] == "inconclusive"


def test_crash_every_case_is_inconclusive(demo: ModuleType) -> None:
    crashed = [
        {**r, "status": "error", "value": None, "message": "judge timed out"}
        for r in rows("candidate-noise.jsonl")
    ]
    assert demo.run(crashed, CONFIG, BASELINES)["report"]["verdict"] == "inconclusive"


def test_zero_on_one_case_trips_the_floor(demo: ModuleType) -> None:
    r = [
        {**x, "value": 0.0} if (x["case"], x["metric"]) == ("refund-policy", "accuracy") else x
        for x in rows("candidate-noise.jsonl")
    ]
    rules = {o["rule"]: o["outcome"] for o in demo.run(r, CONFIG, BASELINES)["report"]["rules"]}
    assert rules["absolute:accuracy"] == "fail"


def test_slow_latency_fails_only_the_optional_rule(demo: ModuleType) -> None:
    r = [
        {**x, "value": x["value"] * 2} if x["metric"] == "latency_ms" else x
        for x in rows("candidate-noise.jsonl")
    ]
    rep = demo.run(r, CONFIG, BASELINES)["report"]
    assert rep["verdict"] == "pass"
    assert any(o["metric"] == "latency_ms" and o["outcome"] == "fail" for o in rep["rules"])


def test_run_json_reports_errors(demo: ModuleType) -> None:
    payload = json.dumps({"results": [], "config": "rules: 7", "baselines": []})
    out = json.loads(demo.run_json(payload))
    assert out["error"].startswith("ConfigError")


def test_status_defaults_to_ok(demo: ModuleType) -> None:
    cfg = "rules: [{type: absolute, metric: m, min: 0}]\n"
    rep = demo.run([{"case": "a", "metric": "m", "value": 1.0}], cfg, [])["report"]
    assert rep["total_results"] == 1
