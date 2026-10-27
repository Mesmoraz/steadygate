"""Flakiness: which cases flip between passing and failing on the same code.

A case is read as a sequence of trials, in the order its results were given (files in
order, then lines within a file). A trial passes when its value is at least 0.5, so a
``passed`` metric of 1/0 works as is. Errored trials are counted on their own and never
break a streak: a judge timeout says nothing about whether the case is flaky.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from steadygate.models import Result, Status
from steadygate.stats import wilson

PASS_AT = 0.5


@dataclass(frozen=True)
class CaseFlakiness:
    case: str
    trials: int
    passes: int
    errors: int
    flips: int
    pass_rate_low: float | None
    pass_rate_high: float | None

    @property
    def fails(self) -> int:
        return self.trials - self.passes

    @property
    def pass_rate(self) -> float | None:
        return self.passes / self.trials if self.trials else None

    @property
    def flip_rate(self) -> float:
        """Share of consecutive trial pairs that changed outcome (0 = steady, 1 = alternating)."""
        return self.flips / (self.trials - 1) if self.trials > 1 else 0.0

    @property
    def status(self) -> str:
        if not self.trials:
            return "broken"
        if self.passes and self.fails:
            return "flaky"
        return "passing" if self.passes else "failing"

    def as_dict(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "status": self.status,
            "trials": self.trials,
            "passes": self.passes,
            "fails": self.fails,
            "errors": self.errors,
            "pass_rate": self.pass_rate,
            "pass_rate_interval": (
                None if self.pass_rate_low is None else [self.pass_rate_low, self.pass_rate_high]
            ),
            "flips": self.flips,
            "flip_rate": self.flip_rate,
        }


_ORDER = {"flaky": 0, "failing": 1, "broken": 2, "passing": 3}


def analyze(
    runs: Iterable[Sequence[Result]], metric: str = "passed", confidence: float = 0.95
) -> list[CaseFlakiness]:
    """Score every case that reported ``metric``; flakiest first."""
    outcomes: dict[str, list[bool]] = {}
    errors: dict[str, int] = {}
    for run in runs:
        for r in run:
            if r.metric != metric or r.status is Status.SKIPPED:
                continue
            outcomes.setdefault(r.case, [])
            errors.setdefault(r.case, 0)
            if r.status is Status.ERROR or r.value is None:
                errors[r.case] += 1
            else:
                outcomes[r.case].append(r.value >= PASS_AT)

    cases = []
    for case, seq in outcomes.items():
        passes = sum(seq)
        flips = sum(a != b for a, b in pairwise(seq))
        low, high = wilson(passes, len(seq), confidence) if seq else (None, None)
        cases.append(CaseFlakiness(case, len(seq), passes, errors[case], flips, low, high))
    cases.sort(key=lambda c: (_ORDER[c.status], -c.flip_rate, c.pass_rate or 0.0, c.case))
    return cases


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _summary(cases: Sequence[CaseFlakiness]) -> str:
    counts = {s: sum(c.status == s for c in cases) for s in _ORDER}
    return ", ".join(f"{n} {s}" for s, n in counts.items() if n) or "no cases"


def _row(c: CaseFlakiness) -> tuple[str, str, str, str, str]:
    interval = (
        "" if c.pass_rate_low is None else f" ({_pct(c.pass_rate_low)}-{_pct(c.pass_rate_high)})"
    )
    err = f", {c.errors} errored" if c.errors else ""
    return (
        c.case,
        c.status,
        f"{c.passes}/{c.trials}{err}",
        f"{_pct(c.pass_rate)}{interval}",
        f"{c.flips} ({c.flip_rate:.0%})",
    )


def text(cases: Sequence[CaseFlakiness], metric: str) -> str:
    lines = [f"steadygate flaky: {_summary(cases)} (metric: {metric})"]
    shown = [c for c in cases if c.status != "passing"]
    if shown:
        header = ("case", "status", "passed", "pass rate", "flips")
        rows = [header, *(_row(c) for c in shown)]
        widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
        for r in rows:
            cells = "  ".join(v.ljust(w) for v, w in zip(r, widths, strict=True))
            lines.append(f"  {cells}".rstrip())
    flaky = [c.case for c in cases if c.status == "flaky"]
    if flaky:
        lines += ["", "Quarantine candidates:", *(f"  - {case}" for case in flaky)]
    return "\n".join(lines).rstrip()


def markdown(cases: Sequence[CaseFlakiness], metric: str) -> str:
    lines = [f"### Steadygate flakiness: {_summary(cases)}", ""]
    shown = [c for c in cases if c.status != "passing"]
    if shown:
        lines += ["| Case | Status | Passed | Pass rate | Flips |", "|---|---|---|---|---|"]
        for c in shown:
            case, *rest = _row(c)
            cells = [f"`{case.replace('|', '/')}`", *rest]
            lines.append("| " + " | ".join(cells) + " |")
    else:
        lines.append("Every case gave the same outcome on every trial.")
    lines += ["", f"<sub>metric `{metric}` · a trial passes at a value of {PASS_AT} or more</sub>"]
    return "\n".join(lines) + "\n"
