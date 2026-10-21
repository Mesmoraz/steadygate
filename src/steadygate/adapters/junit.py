"""JUnit XML adapter (pytest ``--junitxml``, Jest, Playwright, Maven Surefire, ...).

Each ``<testcase>`` becomes two results:

* ``passed``: 1.0 if it passed, 0.0 on ``<failure>``
* ``duration_s``: the ``time`` attribute, when present

``<error>`` means the test could not run properly (setup crash, fixture error), so it is
recorded as ``Status.ERROR`` rather than as a failure. ``<skipped>`` is ignored.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from steadygate.models import Result, Status


def _case_id(tc: ET.Element) -> str:
    classname = tc.get("classname", "")
    name = tc.get("name", "")
    return f"{classname}::{name}" if classname else name


def load(path: Path) -> list[Result]:
    root = ET.parse(path).getroot()
    results: list[Result] = []
    for tc in root.iter("testcase"):
        case = _case_id(tc)
        if tc.find("skipped") is not None:
            results.append(Result(case, "passed", None, Status.SKIPPED))
            continue
        error = tc.find("error")
        if error is not None:
            message = error.get("message", "") or (error.text or "").strip()[:200]
            results.append(Result(case, "passed", None, Status.ERROR, message))
            continue
        passed = 0.0 if tc.find("failure") is not None else 1.0
        results.append(Result(case, "passed", passed))
        time = tc.get("time")
        if time:
            results.append(Result(case, "duration_s", float(time)))
    return results
