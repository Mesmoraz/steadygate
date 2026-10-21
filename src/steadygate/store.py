"""Baseline history, kept in a local SQLite file.

Only ``Status.OK`` results are stored: a broken run must never become part of the baseline.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from steadygate.models import Result, Status

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,  -- recording order, newest = highest
    run_id      TEXT NOT NULL UNIQUE,
    recorded_at TEXT NOT NULL,
    ref         TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS results (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    value  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_results_metric ON results(metric, run_id);
"""


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.parent != Path():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record(self, run_id: str, results: Iterable[Result], ref: str = "") -> int:
        """Store a run's usable results. Re-recording the same run_id replaces it."""
        rows = [
            (run_id, r.case, r.metric, r.value)
            for r in results
            if r.status is Status.OK and r.value is not None
        ]
        now = datetime.now(UTC).isoformat()
        with self._conn:
            self._conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            self._conn.execute(
                "INSERT INTO runs (run_id, recorded_at, ref) VALUES (?, ?, ?)", (run_id, now, ref)
            )
            self._conn.executemany(
                "INSERT INTO results (run_id, case_id, metric, value) VALUES (?, ?, ?, ?)", rows
            )
        return len(rows)

    def run_aggregates(
        self, metric: str, window: int, exclude_run: str | None = None
    ) -> list[float]:
        """Mean of ``metric`` per run, for the most recent ``window`` runs that have it."""
        rows = self._conn.execute(
            """
            SELECT AVG(res.value)
            FROM results res JOIN runs r ON r.run_id = res.run_id
            WHERE res.metric = ? AND res.run_id != ?
            GROUP BY res.run_id
            ORDER BY MAX(r.seq) DESC
            LIMIT ?
            """,
            (metric, exclude_run or "", window),
        ).fetchall()
        return [float(v) for (v,) in rows]

    def run_count(self) -> int:
        (n,) = self._conn.execute("SELECT COUNT(*) FROM runs").fetchone()
        return int(n)
