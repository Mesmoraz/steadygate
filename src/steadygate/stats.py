"""Summary statistics for a list of measurements.

The median and MAD (median absolute deviation) are the robust pair: one wild sample barely
moves them, which is why baselines are built on them rather than on the mean and stdev.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Summary:
    n: int
    mean: float
    median: float
    mad: float
    stdev: float
    min: float
    p25: float
    p75: float
    p95: float
    max: float

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _percentile(ordered: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks (numpy's default)."""
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def mad(values: Sequence[float]) -> float:
    centre = statistics.median(values)
    return float(statistics.median(abs(v - centre) for v in values))


def summarize(values: Iterable[float]) -> Summary:
    ordered = sorted(float(v) for v in values)
    if not ordered:
        raise ValueError("no values to summarize")
    return Summary(
        n=len(ordered),
        mean=statistics.fmean(ordered),
        median=float(statistics.median(ordered)),
        mad=mad(ordered),
        stdev=statistics.stdev(ordered) if len(ordered) > 1 else 0.0,
        min=ordered[0],
        p25=_percentile(ordered, 0.25),
        p75=_percentile(ordered, 0.75),
        p95=_percentile(ordered, 0.95),
        max=ordered[-1],
    )
