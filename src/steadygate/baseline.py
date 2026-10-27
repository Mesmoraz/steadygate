"""Suggest a gate rule from recorded history.

The limit is set from how much the metric naturally moves between runs: twice the
run-to-run noise (MAD/median), and never tighter than 5%, so ordinary noise doesn't fail
the gate but a real regression does.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from steadygate.stats import mad

DIRECTIONS = ("higher_is_better", "lower_is_better")
MIN_LIMIT = 0.05
NOISE_MULTIPLIER = 2.0


@dataclass(frozen=True)
class Suggestion:
    metric: str
    history_runs: int
    median: float | None
    mad: float | None
    noise: float | None
    rule: dict[str, Any] | None
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "history_runs": self.history_runs,
            "median": self.median,
            "mad": self.mad,
            "noise": self.noise,
            "suggested": self.rule,
            "rationale": self.rationale,
        }


# Name fragments of metrics where a smaller number is the better one.
_LOWER_IS_BETTER = ("latency", "duration", "time", "cost", "error", "retries", "memory", "rss")
_LOWER_SUFFIXES = ("_ms", "_s", "_sec", "_bytes", "_mb")


def guess_direction(metric: str) -> str:
    """A best guess from the metric's name; pass a direction explicitly when it's wrong."""
    name = metric.lower()
    if any(part in name for part in _LOWER_IS_BETTER) or name.endswith(_LOWER_SUFFIXES):
        return "lower_is_better"
    return "higher_is_better"


def _ceil_to_hundredth(x: float) -> float:
    # The epsilon keeps an exact 0.05 from rounding up to 0.06 through float error.
    return math.ceil(x * 100 - 1e-9) / 100


def suggest(metric: str, history: Sequence[float], direction: str, min_history: int) -> Suggestion:
    if direction not in DIRECTIONS:
        raise ValueError(f"'direction' must be one of {', '.join(DIRECTIONS)}")
    n = len(history)
    if n < min_history:
        return Suggestion(metric, n, None, None, None, None, f"{n}/{min_history} runs recorded")

    median = float(statistics.median(history))
    spread = mad(history)
    noise = spread / abs(median) if median else 0.0
    limit = _ceil_to_hundredth(max(MIN_LIMIT, NOISE_MULTIPLIER * noise))
    rule = {
        "type": "relative_delta",
        "metric": metric,
        "direction": direction,
        "max_regression": limit,
    }
    rationale = f"run-to-run noise is ±{noise:.1%} (MAD/median); limit set at 2× noise, min 5%"  # noqa: RUF001
    return Suggestion(metric, n, median, spread, noise, rule, rationale)
