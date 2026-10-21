"""Companion sessions: one session is one run.

A session starts its collectors, samples them on a background thread every ``interval_s``,
accepts metrics your app pushes, and becomes fixed when stopped. Samples are written to the
store every ``flush_every_s`` so a crash loses at most that much data.
"""

from __future__ import annotations

import builtins
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from steadygate import collectors
from steadygate.collectors import Collector
from steadygate.models import Result, Status
from steadygate.store import Store

Clock = Callable[[], float]
Wait = Callable[[threading.Event, float], bool]
Builder = Callable[[str, dict[str, Any]], Collector]


def _event_wait(stop: threading.Event, timeout: float) -> bool:
    return stop.wait(timeout)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SessionState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"  # the companion restarted while it was running


class SessionError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Session:
    id: str
    name: str
    state: SessionState
    started_at: str
    stopped_at: str | None
    config: dict[str, Any]
    results: list[Result] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _pushes: int = 0
    _flushed: int = 0

    def add(self, results: list[Result]) -> None:
        with self._lock:
            self.results.extend(results)

    def snapshot(self) -> list[Result]:
        with self._lock:
            return list(self.results)

    def ok_values(self, metric: str) -> list[float]:
        return [
            r.value
            for r in self.snapshot()
            if r.metric == metric and r.status is Status.OK and r.value is not None
        ]

    def metrics(self) -> list[str]:
        return list(dict.fromkeys(r.metric for r in self.snapshot()))

    def summary_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state.value,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "samples": len(self.snapshot()),
        }

    def detail_dict(self) -> dict[str, Any]:
        return self.summary_dict() | {
            "config": self.config,
            "results": [
                {
                    "case": r.case,
                    "metric": r.metric,
                    "value": r.value,
                    "status": r.status.value,
                    "message": r.message,
                }
                for r in self.snapshot()
            ],
        }


class Sampler(threading.Thread):
    def __init__(
        self,
        session: Session,
        sources: list[Collector],
        interval_s: float,
        on_flush: Callable[[Session], None],
        flush_every_s: float,
        clock: Clock = time.monotonic,
        wait: Wait = _event_wait,
    ) -> None:
        super().__init__(name=f"sampler-{session.id}", daemon=True)
        self.session = session
        self.sources = sources
        self.interval_s = interval_s
        self.on_flush = on_flush
        self.flush_every_s = flush_every_s
        self.clock = clock
        self.wait = wait
        self._stop_event = threading.Event()

    def _tick(self, elapsed: float) -> None:
        batch: list[Result] = []
        for c in self.sources:
            try:
                batch.extend(c.sample(elapsed))
            except Exception as exc:  # a collector bug must show up as data, not vanish
                batch.append(Result(f"t={int(elapsed)}", c.id, None, Status.ERROR, str(exc)))
        self.session.add(batch)

    def run(self) -> None:
        t0 = last_flush = self.clock()
        # Wait before the first sample, not after: a CPU % taken right after the collector
        # primed its counter covers a near-zero window and is noise.
        while not self.wait(self._stop_event, self.interval_s):
            self._tick(self.clock() - t0)
            if self.clock() - last_flush >= self.flush_every_s:
                self.on_flush(self.session)
                last_flush = self.clock()

    def stop(self) -> None:
        self._stop_event.set()
        self.join()


class SessionManager:
    def __init__(
        self,
        store: Store,
        clock: Clock = time.monotonic,
        wait: Wait = _event_wait,
        flush_every_s: float = 5.0,
        builder: Builder = collectors.build,
    ) -> None:
        self.store = store
        self.clock = clock
        self.wait = wait
        self.flush_every_s = flush_every_s
        self.builder = builder
        self._sessions: dict[str, Session] = {}
        self._samplers: dict[str, Sampler] = {}
        self._lock = threading.Lock()

    def _flush(self, session: Session) -> None:
        with session._lock:
            pending = session.results[session._flushed :]
            session._flushed = len(session.results)
        if pending:
            self.store.append_samples(session.id, pending)

    def _save(self, s: Session) -> None:
        self.store.save_session(s.id, s.name, s.state.value, s.started_at, s.stopped_at, s.config)

    def create(
        self, name: str, collector_specs: builtins.list[dict[str, Any]], interval_s: float
    ) -> Session:
        sources: builtins.list[Collector] = []
        try:
            for spec in collector_specs:
                sources.append(self.builder(spec["id"], dict(spec.get("params") or {})))
        except Exception:
            for c in sources:
                c.close()
            raise
        config = {"collectors": collector_specs, "interval_s": interval_s}
        session = Session(
            f"s_{secrets.token_hex(4)}", name, SessionState.RUNNING, _now(), None, config
        )
        self._save(session)
        sampler = Sampler(
            session, sources, interval_s, self._flush, self.flush_every_s, self.clock, self.wait
        )
        with self._lock:
            self._sessions[session.id] = session
            self._samplers[session.id] = sampler
        sampler.start()
        return session

    def get(self, session_id: str) -> Session:
        with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id]
        for row in self.store.load_sessions():
            if row["id"] == session_id:
                return self._load(row)
        raise SessionError(f"no session {session_id!r}", 404)

    def _load(self, row: dict[str, Any]) -> Session:
        session = Session(
            row["id"],
            row["name"],
            SessionState(row["state"]),
            row["started_at"],
            row["stopped_at"],
            row["config"],
            self.store.load_samples(row["id"]),
        )
        session._flushed = len(session.results)
        with self._lock:
            return self._sessions.setdefault(session.id, session)

    def list(self) -> builtins.list[Session]:
        return [self.get(row["id"]) for row in self.store.load_sessions()]

    def push(self, session_id: str, items: builtins.list[tuple[str, float, str | None]]) -> int:
        session = self.get(session_id)
        if session.state is not SessionState.RUNNING:
            raise SessionError(f"session {session_id} is {session.state.value}", 409)
        with session._lock:
            for metric, value, case in items:
                if case is None:
                    session._pushes += 1
                    case = f"push-{session._pushes}"
                session.results.append(Result(case, metric, value))
        return len(items)

    def stop(self, session_id: str) -> Session:
        session = self.get(session_id)
        with self._lock:
            sampler = self._samplers.pop(session_id, None)
        if sampler is None:
            return session
        sampler.stop()
        for c in sampler.sources:
            c.close()
        self._flush(session)
        session.state = SessionState.STOPPED
        session.stopped_at = _now()
        self._save(session)
        return session

    def recover(self) -> int:
        return self.store.mark_running_interrupted()

    def shutdown(self) -> None:
        with self._lock:
            running = builtins.list(self._samplers)
        for session_id in running:
            self.stop(session_id)
