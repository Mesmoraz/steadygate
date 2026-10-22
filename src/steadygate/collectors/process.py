"""Sample CPU, memory, threads and handles of a running process with psutil.

This works for any process on the same machine (Windows, Linux, macOS), so the app
under test needs no changes.
"""

from __future__ import annotations

import contextlib
import difflib
import sys

import psutil

from steadygate.collectors.base import CollectorError, CollectorInfo, MetricInfo
from steadygate.models import Result, Status

_ACCESS_DENIED = "access denied; run steadygate as administrator to sample this process"


def _same_name(candidate: str | None, wanted: str) -> bool:
    if not candidate:
        return False
    c, w = candidate.lower(), wanted.lower()
    return c in (w, f"{w}.exe")


def _find_by_name(name: str) -> psutil.Process:
    matches: list[tuple[float, psutil.Process]] = []
    names: set[str] = set()
    for p in psutil.process_iter(["name", "create_time"]):
        pname = p.info["name"]
        if pname:
            names.add(pname)
        if _same_name(pname, name):
            matches.append((p.info["create_time"] or float("inf"), p))
    if not matches:
        lowered = {n.lower(): n for n in names}
        close = difflib.get_close_matches(name.lower(), lowered, n=5, cutoff=0.5)
        raise CollectorError(f"no process named {name!r}", [lowered[c] for c in close])
    # Several processes can share a name (browsers, the venv launcher); the oldest is
    # the predictable choice.
    return min(matches, key=lambda m: m[0])[1]


class ProcessCollector:
    id = "process"

    def __init__(self, pid: int | None = None, name: str | None = None) -> None:
        if pid is None and name is None:
            raise CollectorError("process collector needs a pid or name")
        if pid is not None:
            if isinstance(pid, bool) or not isinstance(pid, int):
                raise CollectorError(f"'pid' must be an integer, got {pid!r}")
            try:
                self._proc = psutil.Process(pid)
            except psutil.NoSuchProcess as exc:
                raise CollectorError(f"no process with pid {pid}") from exc
        else:
            assert name is not None
            self._proc = _find_by_name(str(name))
        self.pid: int = self._proc.pid
        self._gone = False
        # psutil's first cpu_percent() call always returns 0.0; prime it now so the
        # first real sample is meaningful.
        with contextlib.suppress(psutil.Error):
            self._proc.cpu_percent(None)

    @staticmethod
    def metric_infos() -> list[MetricInfo]:
        metrics = [
            MetricInfo("cpu_pct", "%", "lower"),
            MetricInfo("rss_mb", "MB", "lower"),
            MetricInfo("threads"),
        ]
        if sys.platform == "win32":
            metrics.append(MetricInfo("handles"))
        return metrics

    @classmethod
    def info(cls) -> CollectorInfo:
        return CollectorInfo(
            cls.id,
            True,
            cls.metric_infos(),
            {"pid": "int", "name": "str"},
            "samples a process on this machine",
        )

    def _errors(self, case: str, message: str) -> list[Result]:
        # One per metric: a rule only counts errors for its own metric.
        return [Result(case, m.name, None, Status.ERROR, message) for m in self.metric_infos()]

    def sample(self, elapsed_s: float) -> list[Result]:
        if self._gone:
            return []
        case = f"t={int(elapsed_s)}"
        try:
            if not self._proc.is_running():
                raise psutil.NoSuchProcess(self.pid)
            with self._proc.oneshot():
                values = {
                    "cpu_pct": self._proc.cpu_percent(None),
                    "rss_mb": self._proc.memory_info().rss / (1024 * 1024),
                    "threads": float(self._proc.num_threads()),
                }
                if sys.platform == "win32":
                    values["handles"] = float(self._proc.num_handles())
        except psutil.NoSuchProcess:
            self._gone = True
            return self._errors(case, f"process {self.pid} exited")
        except psutil.AccessDenied:
            return self._errors(case, _ACCESS_DENIED)
        return [Result(case, metric, float(v)) for metric, v in values.items()]

    def close(self) -> None:
        pass


def find_processes(q: str, limit: int = 50) -> list[dict[str, int | str]]:
    needle = q.lower()
    hits: list[dict[str, int | str]] = [
        {"pid": p.info["pid"], "name": p.info["name"]}
        for p in psutil.process_iter(["pid", "name"])
        if p.info["name"] and needle in p.info["name"].lower()
    ]
    hits.sort(key=lambda h: (str(h["name"]).lower(), int(h["pid"])))
    return hits[:limit]
