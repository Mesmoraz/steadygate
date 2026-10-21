from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from steadygate.collectors import Collector, CollectorError
from steadygate.models import Result, Status
from steadygate.sessions import SessionError, SessionManager, SessionState
from steadygate.store import Store


class FakeClock:
    """Time moves only when the sampler waits; after ``max_ticks`` waits it blocks until stop."""

    def __init__(self, max_ticks: int = 3) -> None:
        self.t = 0.0
        self.ticks = 0
        self.max_ticks = max_ticks

    def __call__(self) -> float:
        return self.t

    def wait(self, stop: threading.Event, timeout: float) -> bool:
        self.t += timeout
        self.ticks += 1
        if self.ticks >= self.max_ticks:
            stop.wait()
        return stop.is_set()


class FakeCollector:
    id = "fake"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.closed = False

    def sample(self, elapsed_s: float) -> list[Result]:
        if self.fail:
            raise RuntimeError("sensor unplugged")
        return [Result(f"t={int(elapsed_s)}", "m", elapsed_s)]

    def close(self) -> None:
        self.closed = True


def _builder(made: list[FakeCollector]) -> Callable[[str, dict[str, Any]], Collector]:
    def build(collector_id: str, params: dict[str, Any]) -> Collector:
        if collector_id == "bad":
            raise CollectorError("unknown collector 'bad'")
        c = FakeCollector(**params)
        made.append(c)
        return c

    return build


def _until(cond: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 2
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


@pytest.fixture
def made() -> list[FakeCollector]:
    return []


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def manager(store: Store, clock: FakeClock, made: list[FakeCollector]) -> SessionManager:
    m = SessionManager(store, clock=clock, wait=clock.wait, builder=_builder(made))
    yield m  # type: ignore[misc]
    m.shutdown()


def test_sampler_collects_each_tick(manager: SessionManager, clock: FakeClock) -> None:
    s = manager.create("demo", [{"id": "fake"}], interval_s=1)
    _until(lambda: clock.ticks >= 3)
    s = manager.stop(s.id)
    # The first sample comes one interval after start: psutil's CPU % over a near-zero
    # window right after priming is noise (a 350% spike in manual testing).
    assert s.ok_values("m") == [1.0, 2.0]
    assert [r.case for r in s.results] == ["t=1", "t=2"]
    assert s.state is SessionState.STOPPED


def test_stop_closes_collectors(
    manager: SessionManager, clock: FakeClock, made: list[FakeCollector]
) -> None:
    s = manager.create("", [{"id": "fake"}], 1)
    manager.stop(s.id)
    assert made[0].closed


def test_collector_exception_becomes_error_result(
    manager: SessionManager, clock: FakeClock
) -> None:
    s = manager.create("", [{"id": "fake", "params": {"fail": True}}], 1)
    _until(lambda: clock.ticks >= 1)
    s = manager.stop(s.id)
    assert s.results[0].status is Status.ERROR
    assert (s.results[0].metric, s.results[0].message) == ("fake", "sensor unplugged")


def test_bad_collector_closes_already_built(
    manager: SessionManager, made: list[FakeCollector]
) -> None:
    with pytest.raises(CollectorError):
        manager.create("", [{"id": "fake"}, {"id": "bad"}], 1)
    assert made[0].closed


def test_push_assigns_default_cases(manager: SessionManager) -> None:
    s = manager.create("", [], 1)
    assert manager.push(s.id, [("m", 1.0, None), ("m", 2.0, None), ("m", 3.0, "lvl1")]) == 3
    assert [r.case for r in manager.get(s.id).results] == ["push-1", "push-2", "lvl1"]


def test_push_to_stopped_is_409(manager: SessionManager) -> None:
    s = manager.create("", [], 1)
    manager.stop(s.id)
    with pytest.raises(SessionError) as e:
        manager.push(s.id, [("m", 1.0, None)])
    assert e.value.status_code == 409


def test_unknown_is_404(manager: SessionManager) -> None:
    with pytest.raises(SessionError) as e:
        manager.get("s_nope")
    assert e.value.status_code == 404


def test_flush_writes_to_store(store: Store, clock: FakeClock) -> None:
    m = SessionManager(store, clock=clock, wait=clock.wait, builder=_builder([]), flush_every_s=0)
    s = m.create("", [{"id": "fake"}], 1)
    _until(lambda: len(store.load_samples(s.id)) >= 1)
    m.shutdown()


def test_stop_persists_everything(manager: SessionManager, store: Store) -> None:
    s = manager.create("", [], 1)
    manager.push(s.id, [("m", 1.0, None)])
    manager.stop(s.id)
    assert store.load_samples(s.id) == [Result("push-1", "m", 1.0)]
    assert store.load_sessions()[0]["state"] == "stopped"


def test_stop_twice_is_harmless(manager: SessionManager) -> None:
    s = manager.create("", [], 1)
    first = manager.stop(s.id).stopped_at
    assert manager.stop(s.id).stopped_at == first


def test_recover_marks_interrupted_and_get_loads(store: Store, clock: FakeClock) -> None:
    store.save_session("s_00000001", "old", "running", "2026-10-21T00:00:00+00:00", None, {})
    store.append_samples("s_00000001", [Result("t=0", "m", 5.0)])
    m = SessionManager(store, clock=clock, wait=clock.wait)
    assert m.recover() == 1
    s = m.get("s_00000001")
    assert s.state is SessionState.INTERRUPTED
    assert s.ok_values("m") == [5.0]
    assert [x.id for x in m.list()] == ["s_00000001"]


def test_list_includes_live_and_stored(manager: SessionManager) -> None:
    a = manager.create("a", [], 1)
    b = manager.create("b", [], 1)
    assert {s.id for s in manager.list()} == {a.id, b.id}


def test_session_id_format(manager: SessionManager) -> None:
    s = manager.create("", [], 1)
    assert re.fullmatch(r"s_[0-9a-f]{8}", s.id)


def test_dicts(manager: SessionManager) -> None:
    s = manager.create("demo", [], 1)
    manager.push(s.id, [("m", 1.0, None)])
    summary = s.summary_dict()
    assert summary["samples"] == 1 and summary["state"] == "running"
    detail = s.detail_dict()
    assert detail["results"] == [
        {"case": "push-1", "metric": "m", "value": 1.0, "status": "ok", "message": ""}
    ]
    assert detail["config"] == {"collectors": [], "interval_s": 1}
    assert s.metrics() == ["m"]
