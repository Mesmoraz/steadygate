from __future__ import annotations

from steadygate.models import Result, Status
from steadygate.store import Store


def _save(store: Store, sid: str, state: str, stopped_at: str | None = None) -> None:
    store.save_session(sid, "demo", state, "2026-10-21T00:00:00+00:00", stopped_at, {"x": 1})


def test_session_roundtrip(store: Store) -> None:
    _save(store, "s_1", "running")
    samples = [
        Result("t=0", "rss_mb", 10.0),
        Result("t=1", "rss_mb", 11.0),
        Result("t=2", "rss_mb", None, Status.ERROR, "process 1 exited"),
    ]
    store.append_samples("s_1", samples)
    assert store.load_samples("s_1") == samples
    [row] = store.load_sessions()
    assert row == {
        "id": "s_1",
        "name": "demo",
        "state": "running",
        "started_at": "2026-10-21T00:00:00+00:00",
        "stopped_at": None,
        "config": {"x": 1},
    }


def test_upsert_updates_state(store: Store) -> None:
    _save(store, "s_1", "running")
    store.append_samples("s_1", [Result("t=0", "m", 1.0)])
    _save(store, "s_1", "stopped", "2026-10-21T00:01:00+00:00")
    [row] = store.load_sessions()
    assert row["state"] == "stopped"
    assert row["stopped_at"] == "2026-10-21T00:01:00+00:00"
    assert len(store.load_samples("s_1")) == 1  # upsert keeps samples


def test_load_sessions_newest_first(store: Store) -> None:
    store.save_session("s_a", "", "stopped", "2026-10-21T00:00:00+00:00", None, {})
    store.save_session("s_b", "", "stopped", "2026-10-21T00:05:00+00:00", None, {})
    assert [r["id"] for r in store.load_sessions()] == ["s_b", "s_a"]


def test_mark_running_interrupted(store: Store) -> None:
    _save(store, "s_run", "running")
    _save(store, "s_stop", "stopped")
    assert store.mark_running_interrupted() == 1
    states = {r["id"]: r["state"] for r in store.load_sessions()}
    assert states == {"s_run": "interrupted", "s_stop": "stopped"}


def test_existing_runs_tables_untouched(store: Store) -> None:
    _save(store, "s_1", "running")
    store.record("run1", [Result("a", "acc", 1.0)])
    assert store.run_aggregates("acc", window=10) == [1.0]
