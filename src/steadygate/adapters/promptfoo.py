"""promptfoo adapter (``promptfoo eval --output results.json``).

Each row in ``results.results`` (one test x prompt x provider, repeated with ``--repeat``)
becomes:

* ``passed``: 1.0 when the row's assertions passed, else 0.0
* ``score``: the row's aggregate score
* ``latency_ms`` and ``cost``, when present
* one result per entry in ``namedScores`` (assertions with a ``metric:`` name)

A row that promptfoo marks as an error (``failureReason`` 2: provider crash, timeout,
API error) is recorded as ``Status.ERROR``, so it gates as INCONCLUSIVE, not as a
regression. A plain assertion failure is a 0.0, not an error.

The case is the test's ``description`` (or ``test-<index>``). When the file covers more
than one prompt or provider, the prompt label and provider are appended so every
combination is its own case, for example ``refund policy [gpt-4o-mini | concise]``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from steadygate.models import Result, Status

_FAILURE_ERROR = 2


def _rows(data: Any) -> list[dict[str, Any]]:
    # v3+ nests the rows as {"results": {"results": [...]}}; older files are flatter.
    inner = data.get("results") if isinstance(data, dict) else None
    rows = inner.get("results") if isinstance(inner, dict) else inner
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError("expected a promptfoo output object with a 'results' list")
    return rows


def _provider(row: dict[str, Any]) -> str:
    provider = row.get("provider")
    if isinstance(provider, dict):
        return str(provider.get("label") or provider.get("id") or "")
    return str(provider or "")


def _prompt(row: dict[str, Any]) -> str:
    prompt = row.get("prompt")
    if isinstance(prompt, dict):
        return str(prompt.get("label") or prompt.get("raw") or "")[:60]
    return str(row.get("promptIdx", ""))


def _case_names(rows: list[dict[str, Any]]) -> list[str]:
    bases: list[str] = []
    indexes_by_base: dict[str, set[Any]] = defaultdict(set)
    for i, row in enumerate(rows):
        test = row["testCase"] if isinstance(row.get("testCase"), dict) else {}
        index = row.get("testIdx", i)
        base = str(test.get("description") or f"test-{index}")
        bases.append(base)
        indexes_by_base[base].add(index)

    many_providers = len({_provider(r) for r in rows}) > 1
    many_prompts = len({_prompt(r) for r in rows}) > 1
    names: list[str] = []
    for i, (row, base) in enumerate(zip(rows, bases, strict=True)):
        name = base
        if len(indexes_by_base[base]) > 1:  # two different tests share a description
            name = f"{base} #{row.get('testIdx', i)}"
        suffix = [
            p
            for p, many in ((_provider(row), many_providers), (_prompt(row), many_prompts))
            if many
        ]
        if suffix:
            name = f"{name} [{' | '.join(suffix)}]"
        names.append(name)
    return names


def _is_error(row: dict[str, Any]) -> bool:
    if "failureReason" in row:
        return bool(row["failureReason"] == _FAILURE_ERROR)
    # Files from before failureReason existed: an error with no grading result.
    return bool(row.get("error")) and not row.get("gradingResult")


def load(path: Path) -> list[Result]:
    rows = _rows(json.loads(path.read_text(encoding="utf-8")))
    results: list[Result] = []
    for case, row in zip(_case_names(rows), rows, strict=True):
        if _is_error(row):
            message = str(row.get("error") or "provider error").strip()[:200]
            results.append(Result(case, "passed", None, Status.ERROR, message))
            continue
        results.append(Result(case, "passed", 1.0 if row.get("success") else 0.0))
        for metric, key in (("score", "score"), ("latency_ms", "latencyMs"), ("cost", "cost")):
            if row.get(key) is not None:
                results.append(Result(case, metric, float(row[key])))
        named = row.get("namedScores")
        if isinstance(named, dict):
            for metric, value in named.items():
                if isinstance(value, int | float) and not isinstance(value, bool):
                    results.append(Result(case, str(metric), float(value)))
    return results
