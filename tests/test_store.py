from __future__ import annotations

from pathlib import Path

from steadygate.models import Result, Status
from steadygate.store import Store


def r(case: str, metric: str, value: float | None, status: Status = Status.OK) -> Result:
    return Result(case, metric, value, status)


def test_records_only_ok_results(store: Store) -> None:
    n = store.record(
        "run1",
        [
            r("a", "acc", 1.0),
            r("b", "acc", None, Status.ERROR),
            r("c", "acc", None, Status.SKIPPED),
        ],
    )
    assert n == 1
    assert store.run_aggregates("acc", window=10) == [1.0]


def test_aggregates_are_per_run_means_newest_first(store: Store) -> None:
    store.record("old", [r("a", "acc", 0.5), r("b", "acc", 0.7)])
    store.record("mid", [r("a", "acc", 0.8)])
    store.record("new", [r("a", "acc", 0.9), r("b", "acc", 1.0)])
    assert store.run_aggregates("acc", window=10) == [0.95, 0.8, 0.6]
    assert store.run_aggregates("acc", window=2) == [0.95, 0.8]
    assert store.run_aggregates("acc", window=10, exclude_run="new") == [0.8, 0.6]
    assert store.run_aggregates("other", window=10) == []


def test_rerecording_a_run_replaces_it(store: Store) -> None:
    store.record("run1", [r("a", "acc", 0.1)])
    store.record("run2", [r("a", "acc", 0.5)])
    store.record("run1", [r("a", "acc", 0.9)])
    assert store.run_count() == 2
    assert store.run_aggregates("acc", window=10) == [0.9, 0.5]  # run1 is now newest


def test_persists_and_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "h.db"
    with Store(path) as s:
        s.record("run1", [r("a", "acc", 1.0)])
    with Store(path) as s:
        assert s.run_count() == 1
