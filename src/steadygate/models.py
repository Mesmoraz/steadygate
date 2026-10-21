"""Core data types shared by adapters, the store, rules and the gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum


class Status(StrEnum):
    """Whether a single result produced a usable value."""

    OK = "ok"
    ERROR = "error"  # the run broke (crash, timeout, setup error): no trustworthy value
    SKIPPED = "skipped"  # deliberately not run; ignored by the gate


@dataclass(frozen=True)
class Result:
    """One measurement of one metric for one case in one run."""

    case: str
    metric: str
    value: float | None
    status: Status = Status.OK
    message: str = ""


class Outcome(StrEnum):
    """What a single rule concluded."""

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"  # not enough trustworthy data to decide
    NO_BASELINE = "no_baseline"  # first runs: nothing to compare against yet


class Verdict(IntEnum):
    """The overall decision. The integer value is the process exit code."""

    PASS = 0
    REGRESSION = 1
    INCONCLUSIVE = 2
    CONFIG_ERROR = 3


@dataclass(frozen=True)
class RuleOutcome:
    rule: str
    metric: str
    required: bool
    outcome: Outcome
    detail: str
    observed: float | None = None
    baseline: float | None = None


@dataclass
class GateResult:
    verdict: Verdict
    outcomes: list[RuleOutcome] = field(default_factory=list)
    errors: list[Result] = field(default_factory=list)
    total_results: int = 0
