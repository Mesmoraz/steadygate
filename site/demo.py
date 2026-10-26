"""Engine bridge for the live demo.

Runs unchanged under Pyodide in the browser and under CPython in tests. It must not import
anything beyond the standard library, PyYAML and ``steadygate``.
"""

from __future__ import annotations

import json
from typing import Any

import yaml

from steadygate import config, gate, report
from steadygate.models import Result, Status
from steadygate.store import Store


def _results(rows: list[dict[str, Any]]) -> list[Result]:
    return [
        Result(
            case=str(r["case"]),
            metric=str(r["metric"]),
            value=None if r.get("value") is None else float(r["value"]),
            status=Status(r.get("status", "ok")),
            message=str(r.get("message", "")),
        )
        for r in rows
    ]


def run(
    results: list[dict[str, Any]],
    config_yaml: str,
    baselines: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    """Gate ``results`` against ``baselines`` using the real engine."""
    raw = yaml.safe_load(config_yaml) or {}
    raw["store"] = ":memory:"
    cfg = config.parse(raw)
    store = Store(":memory:")
    try:
        for n, base in enumerate(baselines, start=1):
            store.record(f"base-{n}", _results(base))
        result = gate.evaluate(cfg, _results(results), store)
    finally:
        store.close()
    return {"report": report.as_dict(result), "markdown": report.markdown(result)}


def run_json(payload: str) -> str:
    """JSON-in, JSON-out wrapper for the JS side."""
    try:
        data = json.loads(payload)
        return json.dumps(run(data["results"], data["config"], data["baselines"]))
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
