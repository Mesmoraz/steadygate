"""What every collector looks like, and how it describes itself."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from steadygate.models import Result


@dataclass(frozen=True)
class MetricInfo:
    name: str
    unit: str = ""
    better: Literal["higher", "lower"] | None = None


@dataclass(frozen=True)
class CollectorInfo:
    id: str
    available: bool
    metrics: list[MetricInfo] | Literal["any"]
    params: dict[str, str]
    detail: str = ""


class CollectorError(ValueError):
    """A collector could not be built from the given params."""

    def __init__(self, message: str, suggestions: list[str] | None = None) -> None:
        super().__init__(message)
        self.suggestions: list[str] = suggestions or []


class Collector(Protocol):
    id: str

    def sample(self, elapsed_s: float) -> list[Result]: ...

    def close(self) -> None: ...


@dataclass
class PushCollector:
    """Your app sends its own metrics; there is nothing to sample."""

    id: str = field(default="push", init=False)

    @classmethod
    def info(cls) -> CollectorInfo:
        return CollectorInfo(
            "push", True, "any", {}, "POST /sessions/{id}/metrics with any metric name"
        )

    def sample(self, elapsed_s: float) -> list[Result]:
        return []

    def close(self) -> None:
        pass
