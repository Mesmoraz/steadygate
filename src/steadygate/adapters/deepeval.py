"""DeepEval adapter (the test run JSON that DeepEval saves locally).

DeepEval writes it to ``test_run_<timestamp>.json`` in ``DEEPEVAL_RESULTS_FOLDER`` when that
is set, and always to ``.deepeval/.latest_run_full.json``. Each entry in ``testCases`` and
``conversationalTestCases`` becomes:

* ``passed``: 1.0 when DeepEval marked the case a success, else 0.0
* one result per entry in ``metricsData``, named after the metric (``Answer Relevancy``,
  ``Faithfulness``, ...), with its ``score``
* ``latency_ms`` (from ``completionTime``, in seconds) and ``cost`` (``tokenCost``), when present

A metric that raised (judge timeout, API error) has an ``error`` and is recorded as
``Status.ERROR``, and so is the case's ``passed``: the run broke, so it gates as
INCONCLUSIVE, not as a regression. A metric below its threshold is a plain failure.

The case is the test case's ``name``. Cases that share a name (one test repeated) count as
repeated trials of the same case.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from steadygate.models import Result, Status

_LISTS = ("testCases", "conversationalTestCases")


def is_deepeval(data: Any) -> bool:
    return isinstance(data, dict) and any(isinstance(data.get(k), list) for k in _LISTS)


def _cases(data: Any) -> list[dict[str, Any]]:
    if not is_deepeval(data):
        raise ValueError("expected a DeepEval test run with a 'testCases' list")
    cases = [c for key in _LISTS for c in data.get(key) or []]
    if not all(isinstance(c, dict) for c in cases):
        raise ValueError("every DeepEval test case must be an object")
    return cases


def _number(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def load(path: Path) -> list[Result]:
    results: list[Result] = []
    for i, tc in enumerate(_cases(json.loads(path.read_text(encoding="utf-8")))):
        case = str(tc.get("name") or f"test_case_{tc.get('order', i)}")
        metrics = [m for m in tc.get("metricsData") or [] if isinstance(m, dict)]
        errors = []
        for m in metrics:
            name = str(m.get("name") or "metric")
            if m.get("error"):
                message = str(m["error"]).strip()[:200]
                errors.append(f"{name}: {message}")
                results.append(Result(case, name, None, Status.ERROR, message))
            elif (score := _number(m.get("score"))) is not None:
                results.append(Result(case, name, score))

        if errors:
            results.append(Result(case, "passed", None, Status.ERROR, "; ".join(errors)[:200]))
        elif isinstance(tc.get("success"), bool):
            results.append(Result(case, "passed", 1.0 if tc["success"] else 0.0))

        if (seconds := _number(tc.get("completionTime"))) is not None:
            results.append(Result(case, "latency_ms", seconds * 1000))
        if (cost := _number(tc.get("tokenCost"))) is not None:
            results.append(Result(case, "cost", cost))
    return results
