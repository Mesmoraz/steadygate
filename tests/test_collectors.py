from __future__ import annotations

import os
import subprocess
import time

import psutil
import pytest

from steadygate.collectors import (
    CollectorError,
    ProcessCollector,
    build,
    describe_all,
    find_processes,
)
from steadygate.models import Status


def test_describe_all_lists_process_and_push() -> None:
    infos = {c.id: c for c in describe_all()}
    assert {"process", "push"} <= set(infos)
    rss = next(m for m in infos["process"].metrics if m.name == "rss_mb")  # type: ignore[union-attr]
    assert (rss.unit, rss.better) == ("MB", "lower")
    assert infos["push"].metrics == "any"


def test_process_samples_real_child(busy_proc: subprocess.Popen[bytes]) -> None:
    c = build("process", {"pid": busy_proc.pid})
    time.sleep(0.3)
    out = {r.metric: r for r in c.sample(elapsed_s=1.2)}
    assert out["rss_mb"].value is not None and out["rss_mb"].value > 0
    assert out["rss_mb"].case == "t=1"
    assert out["threads"].value is not None and out["threads"].value >= 1
    assert out["cpu_pct"].value is not None and out["cpu_pct"].value > 0  # counter was primed
    c.close()


def test_process_exits_mid_session(busy_proc: subprocess.Popen[bytes]) -> None:
    c = build("process", {"pid": busy_proc.pid})
    busy_proc.kill()
    busy_proc.wait()
    rs = c.sample(2.0)
    # One ERROR per metric, so every rule on these metrics sees the gap.
    assert {r.metric for r in rs} == {m.name for m in ProcessCollector.metric_infos()}
    assert all(r.status is Status.ERROR for r in rs)
    assert rs[0].message == f"process {busy_proc.pid} exited"
    assert c.sample(3.0) == []  # stops sampling after exit


def test_unknown_name_suggests(busy_proc: subprocess.Popen[bytes]) -> None:
    with pytest.raises(CollectorError) as e:
        build("process", {"name": "pythn"})
    assert any("python" in s.lower() for s in e.value.suggestions)


def test_name_matches_without_exe_suffix(busy_proc: subprocess.Popen[bytes]) -> None:
    name = psutil.Process(busy_proc.pid).name()
    stem = name.removesuffix(".exe")
    assert isinstance(build("process", {"name": stem}), ProcessCollector)


def test_name_picks_oldest_match(
    two_busy_procs: tuple[subprocess.Popen[bytes], subprocess.Popen[bytes]],
) -> None:
    older, _ = two_busy_procs
    name = psutil.Process(older.pid).name()
    same = [
        p
        for p in psutil.process_iter(["name", "create_time"])
        if p.info["name"] == name and p.info["create_time"] is not None
    ]
    oldest = min(same, key=lambda p: p.info["create_time"])
    c = build("process", {"name": name})
    assert isinstance(c, ProcessCollector)
    assert c.pid == oldest.pid


def test_needs_pid_or_name() -> None:
    with pytest.raises(CollectorError, match="pid or name"):
        build("process", {})


def test_bad_pid_type() -> None:
    with pytest.raises(CollectorError, match="pid"):
        build("process", {"pid": "abc"})


def test_missing_pid() -> None:
    with pytest.raises(CollectorError, match="no process"):
        build("process", {"pid": 2**31 - 2})


def test_unknown_param() -> None:
    with pytest.raises(CollectorError, match="bad params"):
        build("process", {"pid": os.getpid(), "colour": "red"})


def test_unknown_collector() -> None:
    with pytest.raises(CollectorError, match="unknown collector"):
        build("gpu", {})


def test_push_collector_samples_nothing() -> None:
    c = build("push", {})
    assert c.sample(1.0) == []
    c.close()


def test_find_processes_matches_substring() -> None:
    me = psutil.Process().name()
    hits = find_processes(me[:4].upper())
    assert any(h["pid"] == os.getpid() for h in hits)
    assert len(find_processes("", limit=3)) <= 3
