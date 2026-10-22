# Steadygate Companion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `steadygate serve`, a local HTTP companion plus dashboard. It samples a running process, accepts pushed metrics, aggregates them, suggests baselines and gates sessions using the existing engine.

**Architecture:** New focused modules (`stats`, `baseline`, `collectors`, `sessions`, `api`, `web/`) sit beside the unchanged engine (`rules`, `gate`, `config`, `adapters`). `store` gains two tables. `report` gains `as_dict`. `cli` gains `serve`. The HTTP API is a thin FastAPI layer that validates input and delegates.

**Tech Stack:**
- Python ≥3.11
- FastAPI, uvicorn and psutil in the optional `companion` extra, plus `httpx` in dev for `TestClient`
- SQLite
- Plain HTML and JS dashboard, with Chart.js from a pinned CDN build

**Spec:** `docs/superpowers/specs/2026-10-21-companion-design.md`

## Global Constraints

- **Quality gates stay:** `ruff check .`, `ruff format --check .`, `mypy` (strict on `src`), and `pytest` with `--cov-fail-under=90`, all green after every task.
- **Optional extra:** the core install keeps `PyYAML` as its only dependency. Companion dependencies live under `[project.optional-dependencies] companion = ["fastapi>=0.110", "uvicorn>=0.29", "psutil>=5.9"]`.
- **Lazy import:** core modules (`cli`, `stats`, `baseline`, `store`) must not import fastapi, uvicorn or psutil at import time.
- **Defaults:** host `127.0.0.1`, port `7777`, `interval_s` 1, and the sample buffer is flushed every 5 s.
- **API version:** every JSON body the API returns is an object containing `"api": 1`.
- **Errors can never make you pass:** sampler and collector failures become `Status.ERROR` results, never missing data. NaN and ±inf values are rejected with 422.
- **Suggest formula:** `max_regression = ceil_to_0.01(max(0.05, 2 * mad/|median|))`, and noise is 0 when the median is 0.
- **Case ids:** sampled values use `t=<int seconds since start>`. Pushed values use the given `case`, or `push-<n>` (1-based, per session) when none is given.
- **Session ids:** `s_` followed by 8 lowercase hex characters.
- **Dashboard:** no build step, light and dark themes, works at 375px width.

## Review Focus

1. **psutil's first `cpu_percent()` call always returns 0.0.** A reasonable user expects the first CPU sample to be real. Prime the counter when the collector is created. A test is in Task 3.
2. **A process name that matches several PIDs** (for example `chrome.exe`). The user expects one predictable target. Use the oldest matching process (lowest `create_time`) and report its pid in `describe()`. A test is in Task 3.
3. **Gating a session that recorded zero OK values for a rule's metric.** The user expects INCONCLUSIVE, never PASS. The existing rules already do this, so Task 7 pins it end to end through the API.
4. **`PUT /config` with an invalid body must not damage `steadygate.yml`.** Validate first, then write to a temp file and replace the original. A test is in Task 7.
5. **Stopping a session twice, or gating it twice.** The user expects the second call to be harmless: stop returns the same stopped session, and gate re-evaluates. A test is in Task 6.

---

## File map

| File | Responsibility |
|---|---|
| `src/steadygate/stats.py` | `Summary` dataclass and `summarize()` |
| `src/steadygate/baseline.py` | `Suggestion` dataclass and `suggest()` |
| `src/steadygate/collectors/__init__.py` | `Collector` protocol, `CollectorInfo`, `MetricInfo`, `CollectorError`, `COLLECTORS` registry, `describe_all()`, `build()` |
| `src/steadygate/collectors/process.py` | `ProcessCollector` (psutil) and `find_processes()` |
| `src/steadygate/collectors/push.py` | `PushCollector` |
| `src/steadygate/store.py` (modify) | adds the `sessions` and `samples` tables and methods |
| `src/steadygate/sessions.py` | `SessionState`, `Session`, `Sampler`, `SessionManager` |
| `src/steadygate/report.py` (modify) | `as_dict()`, with `as_json()` delegating to it |
| `src/steadygate/api.py` | `create_app()` and every route |
| `src/steadygate/web/index.html`, `app.js`, `style.css` | dashboard |
| `src/steadygate/cli.py` (modify) | `serve` subcommand |
| `pyproject.toml`, `.github/workflows/ci.yml`, `README.md` (modify) | extras, CI install, docs |
| `tests/test_stats.py`, `test_baseline.py`, `test_collectors.py`, `test_store_sessions.py`, `test_sessions.py`, `test_api.py`, `test_api_gate.py`, `test_cli_serve.py`, `tests/helpers/busy.py` | tests |

---

### Task 1: Summary statistics

**Files:**
- Create: `src/steadygate/stats.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Summary` with fields `n: int, mean, median, mad, stdev, min, p25, p75, p95, max: float`
  - `Summary.as_dict() -> dict[str, float | int]`
  - `summarize(values: Iterable[float]) -> Summary`
  - `mad(values: Sequence[float]) -> float`

- [ ] **Step 1: Write failing tests**

```python
def test_known_values():
    s = summarize([1, 2, 3, 4, 100])
    assert (s.n, s.mean, s.median, s.mad, s.min, s.max) == (5, 22.0, 3.0, 1.0, 1.0, 100.0)
    assert s.p25 == 2.0 and s.p75 == 4.0
    assert s.p95 == pytest.approx(80.8)

def test_even_n_median():
    assert summarize([1, 2, 3, 4]).median == 2.5

def test_single_value():
    s = summarize([7.0])
    assert (s.n, s.mean, s.median, s.mad, s.stdev, s.p95) == (1, 7.0, 7.0, 0.0, 0.0, 7.0)

def test_constant_values_have_zero_mad():
    assert summarize([5, 5, 5]).mad == 0.0

def test_empty_raises():
    with pytest.raises(ValueError, match="no values"):
        summarize([])

def test_as_dict_keys():
    assert list(summarize([1, 2]).as_dict()) == [
        "n", "mean", "median", "mad", "stdev", "min", "p25", "p75", "p95", "max"]
```

- [ ] **Step 2: Run `pytest tests/test_stats.py -v`.** Expect a FAIL with ImportError.
- [ ] **Step 3: Implement `stats.py`.**
  - Percentiles use linear interpolation between closest ranks, which is the same as numpy's default and `statistics.quantiles(method="inclusive")`. Write `_percentile(sorted_vals, q)` yourself so that n=1 works.
  - `stdev` is the sample stdev, and 0.0 when n=1.
  - `mad` is the median of `|x - median|`.
- [ ] **Step 4: Run `pytest tests/test_stats.py -v`.** Expect a PASS.
- [ ] **Step 5: Commit** with the message `feat: add summary statistics`.

---

### Task 2: Baseline suggestion

**Files:**
- Create: `src/steadygate/baseline.py`
- Test: `tests/test_baseline.py`

**Interfaces:**
- Consumes: `stats.mad`, `statistics.median`
- Produces:
  - `@dataclass(frozen=True) class Suggestion` with fields `metric: str, history_runs: int, median: float | None, mad: float | None, noise: float | None, rule: dict[str, Any] | None, rationale: str`
  - `Suggestion.as_dict()`, which emits the key `"suggested"` for `rule`
  - `suggest(metric: str, history: Sequence[float], direction: str, min_history: int) -> Suggestion`
  - `direction` must be either `"higher_is_better"` or `"lower_is_better"`. Anything else raises `ValueError`.

- [ ] **Step 1: Write failing tests**

```python
def test_not_enough_history():
    s = suggest("acc", [0.9, 0.91], "higher_is_better", min_history=3)
    assert s.rule is None and s.rationale == "2/3 runs recorded"

def test_flat_history_gets_floor():
    s = suggest("rss_mb", [400, 400, 400], "lower_is_better", 3)
    assert s.rule == {"type": "relative_delta", "metric": "rss_mb",
                      "direction": "lower_is_better", "max_regression": 0.05}

def test_noisy_history_widens_limit():
    s = suggest("acc", [0.80, 0.90, 1.00, 0.85, 0.95], "higher_is_better", 3)
    # median 0.90, mad 0.05, noise 0.0556 -> 2x = 0.111 -> ceil 0.12
    assert s.noise == pytest.approx(0.05 / 0.90)
    assert s.rule["max_regression"] == 0.12

def test_zero_median_noise_is_zero():
    assert suggest("errors", [0, 0, 1], "lower_is_better", 3).noise == 0.0

def test_bad_direction():
    with pytest.raises(ValueError):
        suggest("x", [1, 2, 3], "up", 3)
```

- [ ] **Step 2: Run `pytest tests/test_baseline.py -v`.** Expect a FAIL.
- [ ] **Step 3: Implement `suggest`.**
  - Round up to the next 0.01 with `math.ceil(x * 100 - 1e-9) / 100`. The epsilon stops 0.05 from rounding up to 0.06.
  - Rationale text when a rule is suggested: `f"run-to-run noise is ±{noise:.1%} (MAD/median); limit set at 2× noise, min 5%"`.
- [ ] **Step 4: Run `pytest tests/test_baseline.py -v`.** Expect a PASS.
- [ ] **Step 5: Commit** with the message `feat: suggest relative_delta baselines from history`.

---

### Task 3: Collectors and the companion extra

**Files:**
- Modify: `pyproject.toml`
  - add the `companion` extra (see Global Constraints)
  - add `httpx>=0.27` and `types-psutil` to `dev`
  - add mypy `ignore_missing_imports` for `uvicorn` only if mypy needs it
- Modify: `.github/workflows/ci.yml` to change the test job install to `pip install -e ".[dev,companion]"`
- Create: `src/steadygate/collectors/__init__.py`, `process.py` and `push.py`
- Create: `tests/helpers/busy.py`, a script that loops `sum(range(10**5))` for `argv[1]` seconds
- Test: `tests/test_collectors.py`

**Interfaces:**
- Consumes: `models.Result` and `models.Status`
- Produces:
  - `@dataclass(frozen=True) MetricInfo(name: str, unit: str = "", better: str | None = None)`, where `better` is `"higher"`, `"lower"` or None
  - `@dataclass(frozen=True) CollectorInfo(id: str, available: bool, metrics: list[MetricInfo] | Literal["any"], params: dict[str, str], detail: str = "")`
  - `class CollectorError(ValueError)`, which carries `suggestions: list[str]`
  - `class Collector(Protocol)` with members `id: str`, `def sample(self, elapsed_s: float) -> list[Result]` and `def close(self) -> None`
  - `COLLECTORS: dict[str, CollectorType]`. Each registry entry is a class with a `@classmethod info() -> CollectorInfo` and a constructor `(**params)`.
  - `describe_all() -> list[CollectorInfo]`
  - `build(id: str, params: dict[str, Any]) -> Collector`. It raises `CollectorError` for an unknown id or bad params.
  - `ProcessCollector(pid: int | None = None, name: str | None = None)`
    - metrics: `cpu_pct` (unit `%`, better lower), `rss_mb` (`MB`, lower), `threads`, and `handles` (Windows only)
    - attribute `pid: int`
  - `find_processes(q: str, limit: int = 50) -> list[dict[str, int | str]]`, which returns `{"pid","name"}` entries with a case-insensitive substring match
  - `PushCollector()` has `info().metrics == "any"`, and its `sample()` returns `[]`. Pushes go to the session directly.

- [ ] **Step 1: Write failing tests.** First add two fixtures to `tests/conftest.py`, because Task 6 reuses them:
  - `busy_proc`: starts `subprocess.Popen([sys.executable, "tests/helpers/busy.py", "30"])` and kills it afterwards.
  - `two_busy_procs`: two of these, started 0.1 s apart.

```python
def test_describe_all_lists_process_and_push():
    ids = {c.id for c in describe_all()}
    assert {"process", "push"} <= ids

def test_process_samples_real_child(busy_proc):
    c = build("process", {"pid": busy_proc.pid})
    time.sleep(0.3)
    out = {r.metric: r for r in c.sample(elapsed_s=1.2)}
    assert out["rss_mb"].value > 0 and out["rss_mb"].case == "t=1"
    assert out["threads"].value >= 1
    assert out["cpu_pct"].value > 0          # Review Focus 1: counter was primed

def test_process_exits_mid_session(busy_proc):
    c = build("process", {"pid": busy_proc.pid})
    busy_proc.kill(); busy_proc.wait()
    rs = c.sample(2.0)
    assert len(rs) == 1 and rs[0].status is Status.ERROR
    assert rs[0].message == f"process {busy_proc.pid} exited"
    assert c.sample(3.0) == []               # stops sampling after exit

def test_unknown_name_suggests(busy_proc):
    with pytest.raises(CollectorError) as e:
        build("process", {"name": "pythn"})
    assert any("python" in s.lower() for s in e.value.suggestions)

def test_name_picks_oldest_match(two_busy_procs):    # Review Focus 2
    older, newer = two_busy_procs
    name = psutil.Process(older.pid).name()
    assert build("process", {"name": name}).pid == min(
        (p for p in psutil.process_iter(["name", "create_time"]) if p.info["name"] == name),
        key=lambda p: p.info["create_time"]).pid

def test_needs_pid_or_name():
    with pytest.raises(CollectorError, match="pid or name"):
        build("process", {})

def test_unknown_collector():
    with pytest.raises(CollectorError, match="unknown collector"):
        build("gpu", {})
```

- [ ] **Step 2: Run `pip install -e ".[dev,companion]"`, then `pytest tests/test_collectors.py -v`.** Expect a FAIL.
- [ ] **Step 3: Implement the collectors.**
  - `psutil.AccessDenied` during `sample` becomes a single ERROR result with the message `access denied; run steadygate as administrator to sample this process`.
  - `NoSuchProcess` becomes an ERROR with the message `process <pid> exited`, and later calls return `[]`.
  - Suggestions use `difflib.get_close_matches(name, all_names, n=5, cutoff=0.5)`.
- [ ] **Step 4: Run `pytest tests/test_collectors.py -v`.** Expect a PASS.
- [ ] **Step 5: Run `ruff check . && mypy && pytest`.** Expect everything green.
- [ ] **Step 6: Commit** with the message `feat: add process and push collectors`.

---

### Task 4: Session persistence in the store

**Files:**
- Modify: `src/steadygate/store.py`, adding the spec §6 DDL to `SCHEMA` and the new methods
- Test: `tests/test_store_sessions.py`

**Interfaces:**
- Produces these `Store` methods:
  - `save_session(id: str, name: str, state: str, started_at: str, stopped_at: str | None, config: dict[str, Any]) -> None`, an upsert
  - `append_samples(session_id: str, results: Sequence[Result]) -> None`
  - `load_sessions() -> list[dict[str, Any]]`, rows with keys `id, name, state, started_at, stopped_at, config`, newest first
  - `load_samples(session_id: str) -> list[Result]`
  - `mark_running_interrupted() -> int`

- [ ] **Step 1: Write failing tests**
  - `test_session_roundtrip`: save the session, append two OK samples and one ERROR, then check that `load_samples` gives back all three with their status and message intact.
  - `test_upsert_updates_state`: save as `running`, then save as `stopped`. One row comes back, with state `stopped`.
  - `test_mark_running_interrupted`: save one running and one stopped session. The method returns 1, and the running one now has state `interrupted`.
  - `test_existing_runs_tables_untouched`: `record` and `run_aggregates` still work against the same DB file.
- [ ] **Step 2: Run `pytest tests/test_store_sessions.py -v`.** Expect a FAIL.
- [ ] **Step 3: Implement the store methods.** `config` is stored as JSON in `config_json`.
- [ ] **Step 4: Run `pytest tests/test_store.py tests/test_store_sessions.py -v`.** Expect a PASS.
- [ ] **Step 5: Commit** with the message `feat: persist companion sessions and samples`.

---

### Task 5: Sessions, sampler and manager

**Files:**
- Create: `src/steadygate/sessions.py`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Consumes:
  - `collectors.build` and `collectors.Collector`
  - Task 4's `Store` methods
  - `models.Result`
- Produces:
  - `class SessionState(StrEnum)` with values `RUNNING="running"`, `STOPPED="stopped"`, `INTERRUPTED="interrupted"`
  - `class SessionError(Exception)` carrying `status_code: int`, which is 404 or 409
  - `@dataclass Session` with fields `id, name, state, started_at, stopped_at, config: dict, results: list[Result]`
  - `Session.summary_dict() -> dict`, with keys `id, name, state, started_at, stopped_at, samples`, where `samples` is a count
  - `Session.detail_dict() -> dict`, which adds `config` and `results` (a list of `{case, metric, value, status, message}`)
  - `Session.ok_values(metric) -> list[float]`
  - `Session.metrics() -> list[str]`
  - `class Sampler(threading.Thread)` with constructor `(session, collectors, interval_s, on_flush, clock=time.monotonic, wait=threading.Event().wait)` and method `stop()`
  - `class SessionManager(store: Store, clock=..., wait=..., flush_every_s: float = 5.0)` with these methods:
    - `create(name: str, collectors: list[dict], interval_s: float) -> Session`. It raises `CollectorError` from `build`. If one collector fails, any collectors already built are closed.
    - `push(id, items: list[tuple[str, float, str | None]]) -> int`. It returns the number accepted, raises 409 if the session isn't running and 404 if it's unknown.
    - `stop(id) -> Session`, which is idempotent and raises 404 if the session is unknown
    - `get(id) -> Session`, which raises 404 if unknown and lazily loads stopped or interrupted sessions from the store
    - `list() -> list[Session]`
    - `recover() -> int`, which calls `mark_running_interrupted`
    - `shutdown()`, which stops all sessions

- [ ] **Step 1: Write failing tests.** Use a `FakeCollector` that returns `Result(f"t={int(e)}", "m", e)`, plus a fake clock that `wait` advances by `interval_s`.
  - `test_sampler_collects_each_tick`: create a session with the fake collector at an interval of 1, let it run for 3 fake ticks, then stop it. The values are `[0.0, 1.0, 2.0]` or more, and the cases start with `t=`.
  - `test_push_assigns_default_cases`: push twice with no case. The cases are `push-1` and `push-2`.
  - `test_push_to_stopped_is_409` and `test_unknown_is_404`, both via `SessionError.status_code`.
  - `test_flush_writes_to_store`: with `flush_every_s=0`, after one tick `store.load_samples(id)` is non-empty.
  - `test_stop_twice_is_harmless` (Review Focus 5): the second `stop` returns a session with the same `stopped_at`.
  - `test_recover_marks_interrupted_and_get_loads`: save a running session via the store, then make a new manager and call `recover()`. `get(id).state` is INTERRUPTED and the samples are loaded.
  - `test_session_id_format`: the id matches `^s_[0-9a-f]{8}$`.
- [ ] **Step 2: Run `pytest tests/test_sessions.py -v`.** Expect a FAIL.
- [ ] **Step 3: Implement the sessions module.**
  - A lock guards each session's `results`.
  - The sampler loop is: `elapsed = clock() - t0`, then add `sample(elapsed)` for each collector, then flush the pending results once `flush_every_s` has passed, then `wait(interval_s)` until stopped.
  - A collector exception during sampling becomes a `Status.ERROR` result with `metric=<collector id>` and `message=str(exc)`.
  - `stop` joins the thread, closes the collectors, flushes, and saves the state.
- [ ] **Step 4: Run `pytest tests/test_sessions.py -v`.** Expect a PASS with no real sleeps, so the suite takes under 2 s.
- [ ] **Step 5: Commit** with the message `feat: add companion sessions and sampler`.

---

### Task 6: API part 1 — collectors, processes, sessions, stats

**Files:**
- Create: `src/steadygate/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes:
  - `SessionManager` and `SessionError` from Task 5
  - `collectors.describe_all`, `find_processes` and `CollectorError` from Task 3
  - `stats.summarize` from Task 1
- Produces:
  - `create_app(config_path: Path, manager: SessionManager | None = None, allow_origins: Sequence[str] = ()) -> FastAPI`
  - When `manager` is None, the app opens `Store(config.store)`, builds a manager and calls `recover()` on startup.
  - The app keeps `app.state.manager`, `app.state.config` (a `Config`) and `app.state.config_path`.
  - It defines routes from spec §4 for `/collectors`, `/processes`, `/sessions*`, `/metrics`, `/stop` and `/stats`.

- [ ] **Step 1: Write failing tests.** Use a fixture that writes a `steadygate.yml` (one `relative_delta` rule on `m`) into `tmp_path`, plus a `TestClient(create_app(path))`.
  - `test_collectors_lists_metrics`: `GET /collectors` returns 200, the body has `api == 1`, and the `process` entry includes `rss_mb` with `better == "lower"`.
  - `test_processes_search`: `GET /processes?q=python` includes `os.getpid()`.
  - `test_push_only_flow`:
    - `POST /sessions {"collectors": []}` returns 200 with an id
    - `POST /sessions/{id}/metrics` with `[{"metric":"m","value":1},{"metric":"m","value":3}]` returns `{"accepted": 2}`
    - `POST /stop` returns `state == "stopped"`
    - `GET /stats?metric=m` gives `median == 2.0` and `n == 2`
  - `test_stats_all_metrics`: without `metric`, the body's `stats` is keyed by metric name.
  - `test_stats_unknown_metric_404`.
  - `test_process_session_samples`: start a session against the `busy_proc` pid with `interval_s` 0.2, wait 0.7 s, stop it, and `rss_mb` has `n >= 2`.
  - `test_unknown_process_422_with_suggestions`: the body's `detail.suggestions` is a list.
  - `test_nan_rejected_422`: the raw body `'{"metric":"m","value":NaN}'` gives 422. Also check `1e999`.
  - `test_push_to_stopped_409` and `test_unknown_session_404`.
  - `test_stop_twice_ok` (Review Focus 5).
  - `test_cors_only_allowed_origin`: a request with `Origin: http://evil` gets no `access-control-allow-origin` header, and one from an `--allow-origin` value does.
- [ ] **Step 2: Run `pytest tests/test_api.py -v`.** Expect a FAIL.
- [ ] **Step 3: Implement the routes.**
  - Validate request bodies with pydantic models, using `allow_inf_nan=False` on `value`.
  - Map `SessionError` to its own status code, and `CollectorError` to 422 with `{"message", "suggestions"}`.
  - Every response is built with `_ok(**fields)`, which adds `"api": 1`.
- [ ] **Step 4: Run `pytest tests/test_api.py -v`.** Expect a PASS.
- [ ] **Step 5: Commit** with the message `feat: companion API for sessions and stats`.

---

### Task 7: API part 2 — record, gate, suggest, config

**Files:**
- Modify: `src/steadygate/report.py` to add `as_dict(result) -> dict[str, Any]`. `as_json` becomes `json.dumps(as_dict(result), indent=2)` and its output must not change; `tests/test_report.py` stays green.
- Modify: `src/steadygate/api.py`
- Test: `tests/test_api_gate.py`

**Interfaces:**
- Consumes:
  - `gate.evaluate(config, results, history, run_id)`
  - `Store.record`
  - `Store.run_aggregates`
  - `baseline.suggest`
  - `config.parse(raw, base_dir)`
- Produces these routes:
  - `POST /sessions/{id}/record` returns `{"recorded": n, "runs": run_count}`, and gives 409 if the session is running.
  - `POST /sessions/{id}/gate?record=` returns `as_dict(result) | {"code": int(verdict), "interrupted": bool, "recorded": bool}`.
  - `GET /baseline/suggest?metric=&direction=` returns `Suggestion.as_dict()`. When `direction` is omitted, use the `better` hint from `describe_all()`, mapping `lower` to `lower_is_better`; otherwise default to `higher_is_better`.
  - `GET /config` returns `{"yaml": str}`.
  - `PUT /config` takes `{"yaml": str}` and returns `{"yaml": str, "rules": n}`.

- [ ] **Step 1: Write failing tests.** Use the same fixture as Task 6, with `min_history: 3`.
  - `test_full_flow_pass_then_regression`:
    - record three push sessions with `m` values around 100 (100, 101, 99)
    - `GET /baseline/suggest?metric=m` gives `suggested.max_regression == 0.05`
    - gate a session with `m=100`: `verdict == "PASS"` and `code == 0`
    - gate a session with `m=80`: `verdict == "REGRESSION"` and `code == 1`
  - `test_no_baseline_before_history`: `code == 0`, and the outcome is `no_baseline`.
  - `test_gate_with_no_values_is_inconclusive` (Review Focus 3): a session with only metric `other` gives `code == 2`.
  - `test_gate_stops_running_session`: after the gate call, `GET /sessions/{id}` shows `state == "stopped"`.
  - `test_gate_record_only_on_pass`: with `record=true`, a regression gives `recorded == false` and the run count is unchanged.
  - `test_interrupted_session_gate_flag`: seed a running session through the store, then build a new app. Gating it gives `interrupted == true`.
  - `test_record_running_409`.
  - `test_config_roundtrip`: `PUT` valid YAML with two rules gives `rules == 2`, and `GET` returns the same YAML.
  - `test_config_invalid_keeps_file` (Review Focus 4): `PUT` invalid YAML gives 422, the file's bytes are unchanged, and `app.state.config` is unchanged.
- [ ] **Step 2: Run `pytest tests/test_api_gate.py tests/test_report.py -v`.** Expect a FAIL in `test_api_gate.py` only.
- [ ] **Step 3: Implement the routes.**
  - The gate uses `session.results` and `run_id=session.id`.
  - `PUT /config` does: `yaml.safe_load`, then `config.parse(raw, path.parent)`, then writes `path.with_suffix(".yml.tmp")`, then `os.replace`, then swaps `app.state.config`.
- [ ] **Step 4: Run `pytest -v`.** Expect everything to PASS with coverage at 90% or more.
- [ ] **Step 5: Commit** with the message `feat: companion API for record, gate, suggest and config`.

---

### Task 8: `steadygate serve`

**Files:**
- Modify: `src/steadygate/cli.py`
- Test: `tests/test_cli_serve.py`

**Interfaces:**
- Consumes: `api.create_app`
- Produces:
  - Subcommand `serve [--host 127.0.0.1] [--port 7777] [-c steadygate.yml] [--allow-origin URL ...]`.
  - It imports `uvicorn` and `steadygate.api` inside the handler.
  - A missing extra prints `steadygate: error: the companion needs extra packages: pip install "steadygate[companion]"` and returns 3.
  - It checks the port with a bind attempt before starting uvicorn. If the port is busy, it prints `steadygate: error: port <p> is in use; try --port` and returns 3.
  - A config error returns 3, the same as `run`.
  - It prints `steadygate companion on http://<host>:<port>  (API docs: /docs)`.
  - `main()` must stop requiring the `results` positional for `serve`. Move `config.load` and `load_results` into the `run` and `record` branches.

- [ ] **Step 1: Write failing tests**
  - `test_serve_port_in_use`: hold a socket bound on a free port, then `main(["serve","--port",str(p),"-c",cfg])` returns 3 and stderr contains `in use`.
  - `test_serve_missing_extra`: `monkeypatch.setitem(sys.modules, "uvicorn", None)` makes it return 3, and stderr contains `steadygate[companion]`.
  - `test_serve_starts_uvicorn`: monkeypatch `uvicorn.run` to capture its arguments. The call returns 0 with `host == "127.0.0.1"`, and the app is a FastAPI instance.
  - `test_core_import_is_light`: in a subprocess, `import steadygate.cli` leaves `fastapi` out of `sys.modules`.
- [ ] **Step 2: Run `pytest tests/test_cli_serve.py tests/test_cli.py -v`.** Expect a FAIL in `test_cli_serve.py` only.
- [ ] **Step 3: Implement `serve` and the `main()` restructure.**
- [ ] **Step 4: Run `pytest -v`.** Expect everything to PASS.
- [ ] **Step 5: Commit** with the message `feat: add steadygate serve`.

---

### Task 9: Dashboard

**Files:**
- Create: `src/steadygate/web/index.html`, `app.js` and `style.css`
- Modify: `src/steadygate/api.py` to mount `StaticFiles(directory=web_dir, html=True)` at `/` after the API routes
- Modify: `pyproject.toml` if needed, so hatch includes `web/*` in the wheel
- Test: add `test_dashboard_served` to `tests/test_api.py`

**Interfaces:**
- Consumes: the API endpoints from Tasks 6 and 7 only, using relative URLs.
- Produces: the spec §8 panels.
  - **Collectors**
  - **New session**: process search with a 250 ms debounce on `/processes`, the interval, and Start
  - **Live**: one Chart.js line chart per metric, polling `GET /sessions/{id}` every 1 s while running, and Stop
  - **Stats table**
  - **Baseline**: Record, the run count, and Suggest, which shows the YAML for the suggested rule with an Apply button. Apply calls `GET /config`, appends or replaces the rule for that metric, then calls `PUT /config`.
  - **Gate**: a badge colored by verdict, plus a list of outcomes
  - **Snippets**: curl, Python (`urllib.request`) and JS (`fetch`) for pushing to the current session, each with a copy button
- Chart.js: `https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js`
- Colors are tokens on `:root`, with a dark set under `@media (prefers-color-scheme: dark)`.
- Layout: a single column under 768px with 16px gutters and no horizontal scroll.

- [ ] **Step 1: Write the failing test.** `GET /` returns 200, contains `<title>Steadygate</title>` and has a `<script src="app.js">` tag. `GET /app.js` returns 200.
- [ ] **Step 2: Run `pytest tests/test_api.py::test_dashboard_served -v`.** Expect a FAIL.
- [ ] **Step 3: Write the three static files and mount them.**
- [ ] **Step 4: Run `pytest -v`.** Expect everything to PASS.
- [ ] **Step 5: Manual check.**
  - Run `steadygate serve -c examples/basic/steadygate.yml` and open `http://127.0.0.1:7777` in the browser pane.
  - Start a session against `python.exe`, watch the charts move, stop it, record it three times, then use Suggest, Apply and Gate. Expect a PASS badge.
  - Check at 375px width and in dark mode.
- [ ] **Step 6: Commit** with the message `feat: companion web dashboard`.

---

### Task 10: Docs, example and final verification

**Files:**
- Modify: `README.md`, adding a "Companion" section:
  - install: `pipx install "steadygate[companion]"`
  - `steadygate serve`
  - the dashboard URL
  - a table of endpoints
  - push examples in curl, Python and JS
  - an end-to-end curl walkthrough: start, push, stop, record ×3, suggest, gate
  - a note that Android, web and iOS collectors are next
- Create: `examples/companion/steadygate.yml`, with `relative_delta` on `rss_mb` (lower_is_better, 0.10) and an optional one on `cpu_pct`
- Create: `examples/companion/walkthrough.sh`, the curl walkthrough as a script that runs against a live server

- [ ] **Step 1: Run `ruff check . && ruff format --check . && mypy && pytest`.** Expect everything green with coverage at 90% or more.
- [ ] **Step 2: Run `steadygate serve -c examples/companion/steadygate.yml` in the background, then `bash examples/companion/walkthrough.sh`.**
  - The script's final line should print `verdict: PASS`.
  - Stop the server afterwards.
- [ ] **Step 3: Commit** with the message `docs: companion README and walkthrough`.
