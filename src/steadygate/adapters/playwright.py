"""Playwright JSON reporter adapter (``npx playwright test --reporter=json > results.json``).

Each test (one spec x project, repeated with ``--repeat-each``) becomes:

* ``passed``: 1.0 when Playwright counts it as ``expected`` or ``flaky`` (passed on a
  retry), 0.0 when ``unexpected``. A test marked ``test.fail()`` that fails is a pass.
* ``duration_s``: the duration of the final attempt
* ``retries``: how many retries it took (0 when the first attempt settled it), so a
  rule can catch a suite that only stays green by retrying

A test whose final attempt was ``interrupted`` (the run was stopped) and a test with no
attempts at all (``--max-failures`` or a crash stopped the run before it started) are
recorded as ``Status.ERROR``, so a run that quit halfway gates as INCONCLUSIVE rather than
quietly passing. Errors outside any test (a global setup crash, a spec file that failed to
load) are recorded the same way under the case ``playwright run``. Tests skipped with
``test.skip()`` or ``test.fixme()`` are recorded as ``Status.SKIPPED``.

The case is the file, ``describe`` titles and test title joined with `` > ``, for example
``checkout.spec.ts > cart > applies coupon``. When the file covers more than one project,
the project name is appended: ``checkout.spec.ts > cart > applies coupon [firefox]``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from steadygate.models import Result, Status

RUN_CASE = "playwright run"
_PASSED = {"expected": 1.0, "flaky": 1.0, "unexpected": 0.0}
_SKIP_ANNOTATIONS = {"skip", "fixme"}


def _specs(suite: dict[str, Any], path: tuple[str, ...]) -> Iterator[tuple[str, dict[str, Any]]]:
    title = str(suite.get("title") or "")
    path = (*path, title) if title else path
    for spec in suite.get("specs") or []:
        yield " > ".join((*path, str(spec.get("title", "")))), spec
    for child in suite.get("suites") or []:
        yield from _specs(child, path)


def _message(error: Any) -> str:
    if isinstance(error, dict):
        error = error.get("message") or error.get("value") or ""
    return str(error).strip()[:200]


def _outcome(test: dict[str, Any], last: dict[str, Any]) -> float:
    status = test.get("status")
    if status in _PASSED:
        return _PASSED[status]
    # Reports without a test-level status: compare the last attempt with what was expected.
    return 1.0 if last.get("status") == test.get("expectedStatus", "passed") else 0.0


def _test_results(case: str, test: dict[str, Any]) -> list[Result]:
    attempts = [a for a in test.get("results") or [] if isinstance(a, dict)]
    skip_marked = any(
        isinstance(a, dict) and a.get("type") in _SKIP_ANNOTATIONS
        for a in test.get("annotations") or []
    )
    if not attempts:
        if skip_marked:
            return [Result(case, "passed", None, Status.SKIPPED)]
        return [Result(case, "passed", None, Status.ERROR, "did not run; the run stopped early")]
    last = attempts[-1]
    if last.get("status") == "interrupted":
        return [Result(case, "passed", None, Status.ERROR, "interrupted; the run was stopped")]
    if test.get("status") == "skipped" or last.get("status") == "skipped":
        return [Result(case, "passed", None, Status.SKIPPED)]
    results = [Result(case, "passed", _outcome(test, last))]
    if last.get("duration") is not None:
        results.append(Result(case, "duration_s", float(last["duration"]) / 1000))
    results.append(Result(case, "retries", float(len(attempts) - 1)))
    return results


def load(path: Path) -> list[Result]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("suites"), list):
        raise ValueError("expected a Playwright JSON report with a 'suites' list")

    specs = [s for suite in data["suites"] for s in _specs(suite, ())]
    projects = {
        str(t.get("projectName") or t.get("projectId") or "")
        for _, spec in specs
        for t in spec.get("tests") or []
    }
    results: list[Result] = []
    for name, spec in specs:
        for test in spec.get("tests") or []:
            project = str(test.get("projectName") or test.get("projectId") or "")
            case = f"{name} [{project}]" if len(projects) > 1 and project else name
            results.extend(_test_results(case, test))
    for error in data.get("errors") or []:
        results.append(Result(RUN_CASE, "passed", None, Status.ERROR, _message(error)))
    return results
