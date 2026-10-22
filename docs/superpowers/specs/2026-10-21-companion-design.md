# Steadygate Companion — Design

**Date:** 2026-10-21
**Status:** Draft, awaiting review
**Sub-project:** 1 of N (companion service + web dashboard). Platform collectors and packaging follow as separate sub-projects.

## 1. Intent

Steadygate today is a CLI that gates results files (JUnit/JSONL) against a recorded baseline. The companion turns it into something you **run next to the app you're testing**: a local service with an HTTP API that any language can call, plus a browser dashboard. It must:

1. **Gather metrics**, both sampled from a running process and pushed by the app.
2. **Report what it can gather** on this machine.
3. **Aggregate** metrics: mean, median, MAD, stdev, min, p25, p75, p95, max and n.
4. **Recommend baselines** from recorded history.
5. **Decide pass or fail** using the existing gate engine.

It should be easy to use (one command, then a browser tab) and easy to share (plain HTTP and JSON, open source).

**Said by the user:** cross-platform eventually (Windows, Linux, Android, iOS, other devices); API callable from any language; generic named metrics plus ready-made perf collectors; Windows first, then Android, then web, then iOS.
**Assumed (accepted in design review):** localhost only, no accounts; one session equals one run; a Python implementation reusing the existing engine.

## 2. Approach

**Chosen:** a Python companion (FastAPI + uvicorn + psutil) inside the existing `steadygate` package, installed as the optional extra `steadygate[companion]`. The HTTP API is the long-term contract.

**Rejected for now:**
- **Go/Rust single binary.** It's the ideal distribution shape, but it means rewriting the engine before the API is proven. We may revisit it later behind the same API.
- **Tauri/Electron desktop app.** It still needs a backend underneath, so it's a packaging layer for a later phase.

## 3. Architecture

```
your app / test script / CI ──HTTP JSON──┐
browser dashboard ───────────────────────┤
                                         ▼
                    ┌──────────── steadygate serve ────────────┐
                    │  api/        FastAPI routes (the contract) │
                    │  sessions/   start → collect → stop        │
                    │  collectors/ process (psutil), push        │
                    │  stats/      summary statistics            │
                    │  baseline/   suggest rules from history    │
                    │  gate.py     existing engine (unchanged)   │
                    │  store.py    existing SQLite (+ 2 tables)  │
                    │  web/        static dashboard (no build)   │
                    └────────────────────────────────────────────┘
```

`steadygate serve [--host 127.0.0.1] [--port 7777] [-c steadygate.yml] [--allow-origin URL]` starts a single local process.

### 3.1 Units

| Unit | Purpose | Depends on |
|---|---|---|
| `stats` | Pure functions: `summarize(values) -> Summary` (n, mean, median, mad, stdev, min, p25, p75, p95, max). Raises `ValueError` on empty input. | stdlib only |
| `collectors` | `Collector` protocol with `id`, `describe() -> CollectorInfo` (metrics, units, `better` direction, params, `available`), `sample() -> list[Result]` and `close()`. Implementations: `ProcessCollector` (psutil: `cpu_pct`, `rss_mb`, `threads`, `handles`, the last on Windows only) and `PushCollector` (accepts any metric). Registered in `COLLECTORS`. | psutil, `models` |
| `sessions` | `Session` (id, name, state, buffer of `Result`) and `SessionManager` (create, push, stop, get, list, recover on startup). The `Sampler` thread calls `sample()` every `interval_s`, using an injectable clock and sleep. | `collectors`, `store` |
| `baseline` | `suggest(history: list[float], metric, direction, min_history) -> Suggestion`. | `stats` |
| `api` | FastAPI app factory `create_app(config, store, manager)`. Thin routes that only validate and delegate. | everything above, `gate`, `report` |
| `web` | `index.html`, `app.js`, `style.css`, served at `/`. Charts come from a pinned CDN build of a small chart library. | the API only |
| `store` (extended) | Adds the `sessions` and `samples` tables plus methods to save and load them. Existing tables are unchanged. | sqlite3 |
| `cli` (extended) | Adds the `serve` subcommand. Exits with code 3 if the companion extra isn't installed or the port is in use. | `api` |

**Unchanged:** `models`, `rules`, `gate`, `config`, `adapters`, `report`. The CLI and the server share one engine, so the same data gives the same verdict either way.

## 4. API (version 1)

Every JSON response includes `"api": 1`. Interactive docs are served at `/docs`.

| Method and path | Purpose |
|---|---|
| `GET /collectors` | What can be gathered: each collector's metrics (name, unit, better), params and `available`. |
| `GET /processes?q=` | Running processes `{pid, name}` matching `q`, used by the dashboard's picker. |
| `POST /sessions` | Start a session. Body: `{"name"?, "collectors": [{"id", "params"}], "interval_s"?: 1}`. An empty `collectors` list gives a session that only receives pushed metrics. Returns the session. |
| `GET /sessions` | List sessions (id, name, state, times, sample count). |
| `GET /sessions/{id}` | One session, with its samples. |
| `POST /sessions/{id}/metrics` | Push one `{"metric","value","case"?}` or a list of them. Only allowed while the session is running. |
| `POST /sessions/{id}/stop` | Stop sampling and fix the results. Returns the session plus stats. |
| `GET /sessions/{id}/stats?metric=` | Summary for one metric, or for all metrics keyed by name. |
| `POST /sessions/{id}/record` | Add the session's OK results to the baseline history (`run_id` = session id). |
| `POST /sessions/{id}/gate?record=false` | Verdict JSON in the same shape as `steadygate run --json`, plus `code`. Stops a running session first. With `record=true`, the session is recorded only when the verdict is PASS. |
| `GET /baseline/suggest?metric=&direction=` | Suggested rule from history. |
| `GET /config` / `PUT /config` | Read or replace the active rules, in the `steadygate.yml` schema. `PUT` validates with `config.parse` and writes the file. |
| `GET /` | Dashboard. |

**Case ids.** Sampled values use `case = "t=<seconds since start>"`. Pushed values use the given `case`, or `push-<n>` if none is given. Because the store averages per run, a recorded session's baseline value is its mean, which matches the CLI's behaviour.

## 5. Baseline suggestion

Given the per-run aggregates for a metric (newest `window` runs):

- If there are fewer than `min_history` runs, return `{"suggested": null, "rationale": "k/min_history runs recorded"}`.
- Otherwise set `median = median(history)`, `mad = MAD(history)` and `noise = mad / |median|`. If the median is 0, noise is 0.
- Set `max_regression = round_up(max(0.05, 2 × noise), 0.01)`.
- Suggest `{"type":"relative_delta","metric", "direction", "max_regression"}`.
- `direction` comes from the query, or else from the collector's `better` hint, or else defaults to `higher_is_better`.
- The response also includes `history_runs`, `median`, `mad`, `noise` and a one-line `rationale`.

## 6. Data flow

1. `POST /sessions` creates the session in memory and adds a `sessions` row with state `running`.
2. The Sampler calls each collector once per tick and adds the results to the buffer. Pushed metrics go into the same buffer. The buffer is written to `samples` every 5 s and on stop.
3. `stop` joins the sampler and sets state `stopped`. Results are fixed from then on.
4. `stats` runs `stats.summarize` over the OK values. `record` calls `Store.record`. `gate` calls `gate.evaluate(config, results, store, run_id=session_id)`.

**Schema additions** (additive only, in the same DB file):
```sql
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
  started_at TEXT NOT NULL, stopped_at TEXT, config_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  case_id TEXT NOT NULL, metric TEXT NOT NULL, value REAL,
  status TEXT NOT NULL, message TEXT NOT NULL DEFAULT ''
);
```

## 7. Error handling

The principle is the same as the CLI's: **errors can never make you pass.**

| Situation | Behaviour |
|---|---|
| Target process exits partway through a session | The collector logs an `ERROR` sample with the message "process <pid> exited" and stops sampling. The session keeps running for pushes. If an affected rule would otherwise PASS, it becomes INCONCLUSIVE. |
| Access denied (for example an elevated process) | An `ERROR` sample with the message "access denied; run steadygate as administrator to sample this process". |
| No process matches `name`/`pid` at start | 422 with up to 5 close name matches. |
| Invalid body, NaN or ±inf value | 422 with an error for each field. Nothing is stored. |
| Unknown session | 404. |
| Pushing to a stopped session, or recording a running one | 409 with a message. (Gate stops the session automatically.) |
| `PUT /config` invalid | 422 with the `ConfigError` text. The file is unchanged. |
| Port in use, or the companion extra is missing | `serve` exits with code 3 and a one-line hint. |
| Companion restarted while a session was running | On startup the session's state becomes `interrupted`, and its stored samples can still be read and gated. The gate response includes `"interrupted": true`. |

**Security:** the companion binds to `127.0.0.1` by default. CORS allows only the companion's own origin plus any `--allow-origin` values. There's no auth in v1, because LAN mode with a pairing token is a later sub-project.

## 8. Dashboard

One page with no build step:
- **Collectors panel** showing what this machine can gather.
- **New session:** a process picker (search via `/processes`), the interval, and a Start button.
- **Live view:** one line chart per metric, polling `GET /sessions/{id}` every second, and a Stop button.
- **Stats table** showing every summary field for each metric.
- **Baseline:** a Record button, the history count, and "Suggest baseline", which shows the suggested rule with an "Apply" action that calls `PUT /config`.
- **Gate:** a button and a big PASS / REGRESSION / INCONCLUSIVE / NO_BASELINE badge with the outcome details.
- **Copy-paste snippets** (curl, Python, JS) for pushing metrics to the current session.

It supports both light and dark themes and fits phone width.

## 9. Testing

The existing standards stay: pytest, a 90% coverage gate, ruff and strict mypy.

- **stats:** checked against known values; n=1; all values identical (MAD 0); even and odd n; empty input raises.
- **baseline:** under `min_history`; noisy history widens the limit; flat history gives the 5% floor; zero median; direction resolution.
- **ProcessCollector:** runs against a real child Python process the test starts. Covers sampling, the process exiting partway through, a missing name, and `describe()`.
- **Sampler/SessionManager:** a fake clock and a fake collector, so there are no real sleeps. Covers flush, stop and restart recovery (`interrupted`).
- **API:** `TestClient` runs the end-to-end flow (start → push → stop → stats → record ×3 → suggest → gate PASS → injected regression → gate REGRESSION), plus one test per row of the error table.
- **Dashboard:** smoke tests that `/` and the static assets are served. It's checked once by hand in the browser.
- **CI:** install `.[dev,companion]` across the existing OS matrix.

## 10. Scope

**In:** `steadygate serve`; process and push collectors; every endpoint in §4; sessions and samples persistence; the dashboard in §8; a README section with curl, Python and JS examples.

**Out (later sub-projects, in order):** Android collector over adb; a web/JS push snippet; iOS; LAN mode with a pairing token; packaging (PyInstaller exe, then possibly a Go binary or Tauri tray app); client SDKs.

**Done when:** on Windows, `steadygate serve` and a browser tab are enough to sample a real app, view its stats, record 3 baselines, get a suggested rule, and see REGRESSION when the app gets worse. The same flow also works through curl alone.
