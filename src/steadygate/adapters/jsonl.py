"""Generic JSON Lines adapter.

One object per line::

    {"case": "refund-policy", "metric": "accuracy", "value": 0.9}
    {"case": "refund-policy", "metric": "latency_ms", "value": 812}
    {"case": "shipping-faq", "metric": "accuracy", "status": "error", "message": "judge timeout"}

``status`` defaults to ``ok``; ``value`` is required when status is ``ok``.
"""

from __future__ import annotations

import json
from pathlib import Path

from steadygate.models import Result, Status


def load(path: Path) -> list[Result]:
    results: list[Result] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f"line {lineno}: expected an object")
            try:
                case = str(obj["case"])
                metric = str(obj["metric"])
            except KeyError as exc:
                raise ValueError(f"line {lineno}: missing field {exc}") from exc
            status = Status(obj.get("status", "ok"))
            raw = obj.get("value")
            if status is Status.OK and raw is None:
                raise ValueError(f"line {lineno}: 'value' is required when status is ok")
            value = None if raw is None else float(raw)
            results.append(Result(case, metric, value, status, str(obj.get("message", ""))))
    return results
