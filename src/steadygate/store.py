"""Baseline history, kept in a local SQLite file.

Only ``Status.OK`` results are stored: a broken run must never become part of the baseline.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    stopped_at  TEXT,
    config_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    case_id    TEXT NOT NULL,
    metric     TEXT NOT NULL,
    value      REAL,
    status     TEXT NOT NULL,
    message    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_samples_session ON samples(session_id);
"""


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.parent != Path():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # The companion's sampler threads share this connection; the lock serialises them.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.RLock()
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
        with self._lock, self._conn:
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
        with self._lock:
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

    def recent_cases(self, window: int, exclude_run: str | None = None) -> list[set[str]]:
        """The cases each of the most recent ``window`` runs reported, newest first."""
        with self._lock:
            runs = self._conn.execute(
                "SELECT run_id FROM runs WHERE run_id != ? ORDER BY seq DESC LIMIT ?",
                (exclude_run or "", window),
            ).fetchall()
            return [
                {
                    case
                    for (case,) in self._conn.execute(
                        "SELECT DISTINCT case_id FROM results WHERE run_id = ?", (run_id,)
                    )
                }
                for (run_id,) in runs
            ]

    def run_count(self) -> int:
        with self._lock:
            (n,) = self._conn.execute("SELECT COUNT(*) FROM runs").fetchone()
        return int(n)

    # --- companion sessions -------------------------------------------------------------

    def save_session(
        self,
        id: str,
        name: str,
        state: str,
        started_at: str,
        stopped_at: str | None,
        config: dict[str, Any],
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO sessions (id, name, state, started_at, stopped_at, config_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name, state = excluded.state,
                    stopped_at = excluded.stopped_at, config_json = excluded.config_json
                """,
                (id, name, state, started_at, stopped_at, json.dumps(config)),
            )

    def append_samples(self, session_id: str, results: Sequence[Result]) -> None:
        rows = [(session_id, r.case, r.metric, r.value, r.status.value, r.message) for r in results]
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO samples (session_id, case_id, metric, value, status, message)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )

    def load_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, state, started_at, stopped_at, config_json FROM sessions"
                " ORDER BY started_at DESC, rowid DESC"
            ).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "state": r[2],
                "started_at": r[3],
                "stopped_at": r[4],
                "config": json.loads(r[5]),
            }
            for r in rows
        ]

    def load_samples(self, session_id: str) -> list[Result]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT case_id, metric, value, status, message FROM samples"
                " WHERE session_id = ? ORDER BY rowid",
                (session_id,),
            ).fetchall()
        return [Result(c, m, v, Status(s), msg) for c, m, v, s, msg in rows]

    def mark_running_interrupted(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE sessions SET state = 'interrupted' WHERE state = 'running'"
            )
        return cur.rowcount
