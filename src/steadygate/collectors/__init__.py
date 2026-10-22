"""Collectors gather metrics while a session runs.

Each one describes what it can measure (``info()``) and returns ``Result`` samples on every
tick (``sample()``). Adding a platform, such as Android over adb, means adding a collector
here; the API does not change.
"""

from __future__ import annotations

from typing import Any

from steadygate.collectors.base import (
    Collector,
    CollectorError,
    CollectorInfo,
    MetricInfo,
    PushCollector,
)
from steadygate.collectors.process import ProcessCollector, find_processes

COLLECTORS: dict[str, type[ProcessCollector] | type[PushCollector]] = {
    ProcessCollector.id: ProcessCollector,
    "push": PushCollector,
}

__all__ = [
    "COLLECTORS",
    "Collector",
    "CollectorError",
    "CollectorInfo",
    "MetricInfo",
    "ProcessCollector",
    "PushCollector",
    "build",
    "describe_all",
    "find_processes",
]


def describe_all() -> list[CollectorInfo]:
    return [cls.info() for cls in COLLECTORS.values()]


def build(collector_id: str, params: dict[str, Any]) -> Collector:
    cls = COLLECTORS.get(collector_id)
    if cls is None:
        raise CollectorError(
            f"unknown collector {collector_id!r}; available: {', '.join(COLLECTORS)}"
        )
    try:
        return cls(**params)
    except TypeError as exc:
        raise CollectorError(f"bad params for {collector_id}: {exc}") from exc
