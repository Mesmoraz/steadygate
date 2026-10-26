"""pytest-json-report adapter (``pytest --json-report --json-report-file=report.json``).

Each entry in ``tests`` becomes:

* ``passed``: 1.0 for ``passed``/``xpassed``, 0.0 for ``failed``
* ``duration_s``: the duration of the ``call`` stage, when present
* one result per numeric value the test stored in its ``metadata`` (via the plugin's
  ``json_metadata`` fixture), so a test can report eval scores such as
  ``json_metadata["accuracy"] = 0.92``

``error`` (a setup or teardown crash) is recorded as ``Status.ERROR``. ``skipped`` and
``xfailed`` are recorded as ``Status.SKIPPED``, the same way pytest's JUnit output does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from steadygate.models import Result, Status

_PASSED = {"passed": 1.0, "xpassed": 1.0, "failed": 0.0}
_SKIPPED = {"skipped", "xfailed"}


def _error_message(test: dict[str, Any]) -> str:
    for stage in ("setup", "call", "teardown"):
        info = test.get(stage)
        if not isinstance(info, dict) or info.get("outcome") in (None, "passed"):
            continue
        crash = info.get("crash")
        if isinstance(crash, dict) and crash.get("message"):
            return str(crash["message"])[:200]
        return str(info.get("longrepr", "")).strip()[:200]
    return ""


def load(path: Path) -> list[Result]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("tests"), list):
        raise ValueError("expected a pytest-json-report object with a 'tests' list")
    results: list[Result] = []
    for i, test in enumerate(data["tests"]):
        if not isinstance(test, dict) or "nodeid" not in test:
            raise ValueError(f"tests[{i}]: expected an object with a 'nodeid'")
        case = str(test["nodeid"])
        outcome = test.get("outcome")
        if outcome in _SKIPPED:
            results.append(Result(case, "passed", None, Status.SKIPPED))
            continue
        if outcome == "error":
            results.append(Result(case, "passed", None, Status.ERROR, _error_message(test)))
            continue
        if outcome not in _PASSED:
            raise ValueError(f"tests[{i}]: unknown outcome {outcome!r}")
        results.append(Result(case, "passed", _PASSED[outcome]))
        call = test.get("call")
        if isinstance(call, dict) and call.get("duration") is not None:
            results.append(Result(case, "duration_s", float(call["duration"])))
        metadata = test.get("metadata")
        if isinstance(metadata, dict):
            for metric, value in metadata.items():
                if isinstance(value, int | float) and not isinstance(value, bool):
                    results.append(Result(case, str(metric), float(value)))
    return results
