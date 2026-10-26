# Steadygate

**A statistical release gate for non-deterministic systems: LLM agents, evals, and flaky test suites.**

**[▶ Try the live demo](https://mesmoraz.github.io/steadygate/)**, which runs in your browser with nothing to install.

Your eval passed, and your agent still got worse. Gates like "fail if score < 0.80" break down
when the model, the LLM judge, and the set of cases that ran all vary between runs. They raise
false alarms on noise, and they pass quietly when half the suite crashed.

Steadygate sits **on top of** the eval or test framework you already use. It compares each run
against your own history and gives one of three answers:

| Exit | Verdict | Meaning |
|---|---|---|
| `0` | **PASS** | Within normal variation of the baseline |
| `1` | **REGRESSION** | A required rule failed: this is worse, not noise |
| `2` | **INCONCLUSIVE** | Results errored or were missing, so the gate refuses to call it a pass |
| `3` | config error | Bad config or unreadable results file |

The core rule is that **errors can never make you pass.** A crashed test or a judge timeout
is reported as *inconclusive*, never as a silent green.

> Status: pre-alpha (v0.0.1). The API and config format will change.

## Quick start

```bash
pip install git+https://github.com/Mesmoraz/steadygate
```

Write a `steadygate.yml`:

```yaml
store: .steadygate/history.db
window: 10          # compare against the last 10 recorded runs
min_history: 3      # don't gate until 3 runs exist

rules:
  - type: relative_delta          # mean may not drop >5% below the baseline median
    metric: accuracy
    max_regression: 0.05

  - type: absolute                # hard floor on every case
    metric: accuracy
    min: 0.5
    scope: case

  - type: relative_delta          # tracked, but doesn't block
    metric: latency_ms
    direction: lower_is_better
    max_regression: 0.25
    required: false
```

Record baselines on your main branch and gate on pull requests:

```bash
steadygate record results.jsonl --run-id "$GITHUB_SHA"     # on main
steadygate run results.jsonl --markdown report.md          # on PRs
```

`steadygate run --record` gates first and adds the run to the baseline only if it passes.

### Try the example

```bash
cd examples/basic
for i in 1 2 3; do steadygate record baseline-$i.jsonl --run-id base-$i; done
steadygate run candidate-noise.jsonl        # PASS: -2.2% is within tolerance
steadygate run candidate-regression.jsonl   # REGRESSION: -17.8%
steadygate run candidate-broken.jsonl       # INCONCLUSIVE: one case's judge timed out
steadygate run candidate-missing.jsonl      # INCONCLUSIVE: one case never reported at all

cd ../trials                                # each case run 10 times
steadygate run candidate-noise.jsonl        # PASS: 7/10 against an 80% target is noise
steadygate run candidate-broken.jsonl       # REGRESSION: 3/10 is confidently below 80%
```

Or try these scenarios in the [live demo](https://mesmoraz.github.io/steadygate/), where you can move the scores yourself.

## Companion: run it next to your app

`steadygate serve` starts a small local service with an HTTP API that any language can call,
plus a dashboard in your browser. It can sample a running process (CPU, memory, threads,
handles) and also accept any metric your app sends, such as frame time, load time or eval
score. It summarises them (mean, median, MAD, quartiles, p95), suggests a baseline from your
history, and gives the same PASS / REGRESSION / INCONCLUSIVE verdict as the CLI.

```bash
pipx install "steadygate[companion]"
steadygate serve                      # http://127.0.0.1:7777  (API docs at /docs)
```

Open the dashboard, pick a process, press **Start**, watch the charts, then **Stop**, **Record as
baseline**, **Suggest baseline** and **Run gate**. To do the same from a script, see
[`examples/companion/walkthrough.sh`](examples/companion/walkthrough.sh), which runs the whole
flow with curl.

### API

Every response is JSON and includes `"api": 1`.

| Method and path | What it does |
|---|---|
| `GET /collectors` | What this machine can gather, with units and which direction is better |
| `GET /processes?q=` | Find a process to sample |
| `POST /sessions` | Start a session: `{"collectors": [{"id": "process", "params": {"name": "myapp"}}], "interval_s": 1}`. Leave `collectors` empty to only push. |
| `POST /sessions/{id}/metrics` | Push `{"metric", "value", "case"?}`, or a list of them |
| `POST /sessions/{id}/stop` | Stop sampling; returns stats for every metric |
| `GET /sessions/{id}/stats?metric=` | n, mean, median, MAD, stdev, min, p25, p75, p95, max |
| `POST /sessions/{id}/record` | Add the session to the baseline history |
| `GET /baseline/suggest?metric=` | A `relative_delta` rule sized from run-to-run noise (2× MAD/median, at least 5%) |
| `POST /sessions/{id}/gate?record=` | The verdict, in the same shape as `steadygate run --json` plus `code` |
| `GET /config`, `PUT /config` | Read or replace `steadygate.yml` (validated before it is written) |

### Push metrics from any language

```bash
curl -X POST http://127.0.0.1:7777/sessions/$ID/metrics \
  -H "content-type: application/json" -d '{"metric": "frame_ms", "value": 16.4}'
```

```python
import json, urllib.request

req = urllib.request.Request(
    f"http://127.0.0.1:7777/sessions/{session_id}/metrics",
    data=json.dumps({"metric": "frame_ms", "value": 16.4}).encode(),
    headers={"content-type": "application/json"},
)
urllib.request.urlopen(req)
```

```js
await fetch(`http://127.0.0.1:7777/sessions/${sessionId}/metrics`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ metric: "frame_ms", value: 16.4 }),
});
```

The companion listens on `127.0.0.1` only. It rejects requests from web pages on other origins
unless you allow them with `--allow-origin http://localhost:3000`.

Collectors for Android (over adb), web pages (a JS snippet) and iOS are next on the roadmap.

## Input formats

| Format | Detected by | Produces |
|---|---|---|
| **JSONL** | `.jsonl`, `.ndjson` | `{"case", "metric", "value", "status"?, "message"?}` per line |
| **JUnit XML** | `.xml` | `passed` (1/0) and `duration_s` per test case; `<error>` is treated as a broken run, `<skipped>` is ignored |
| **pytest-json-report** | `.json` with a `tests` list | `passed` and `duration_s` per test, plus every number the test stored with `json_metadata`; setup/teardown errors are a broken run |
| **promptfoo** | `.json` with a `results` key | `passed`, `score`, `latency_ms`, `cost` and each `namedScores` metric per test (× prompt × provider); provider errors and timeouts are a broken run, not a regression |

JUnit covers pytest (`--junitxml`), Jest, Playwright, Vitest, Maven and most CI tools.
A DeepEval adapter is on the roadmap. Use `--format` (`jsonl`, `junit`, `pytest-json`,
`promptfoo`) to override detection.

## GitHub Action

```yaml
# .github/workflows/eval.yml
on:
  push:
    branches: [main]
  pull_request:

jobs:
  gate:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write        # for the PR comment
    steps:
      - uses: actions/checkout@v4
      - run: npx promptfoo eval --repeat 5 --output results.json   # or pytest, Jest, ...
      - uses: Mesmoraz/steadygate@main
        with:
          results: results.json
```

On pushes to the default branch, a passing run is added to the history. On pull requests the
run is gated against that history without changing it, and the report is posted as one PR
comment that is updated on each push. The history lives in the Actions cache, so there is
nothing to host.

| Input | Default | |
|---|---|---|
| `results` | (required) | Results file to gate |
| `config` | `steadygate.yml` | Relative to `working-directory` |
| `format` | detected | `jsonl`, `junit`, `pytest-json` or `promptfoo` |
| `working-directory` | `.` | |
| `record` | `auto` | `auto` records passing runs on default-branch pushes only; or `true` / `false` |
| `history-cache` | `true` | Keep the history store in the Actions cache |
| `cache-key` | `steadygate-history` | Use a different one for each gate in a repository |
| `comment` | `true` | Post the report on pull requests |
| `package` | this action's copy | A pip requirement, to pin a released version |

Outputs are `verdict` (`pass`, `regression`, `inconclusive`, `config-error`), `exit-code` and
`report` (path to the markdown). The step fails on any non-zero code; add
`continue-on-error: true` to keep going and branch on `verdict`. The report is always written
to the job summary, which also covers PRs from forks, where the token cannot comment.

## Rules

- **`absolute`**: `min` and/or `max` on the mean (`scope: aggregate`) or on every case
  (`scope: case`). Use it for deterministic contracts.
- **`relative_delta`**: the mean may not regress more than `max_regression` (a fraction)
  against the **median** of recent runs. The median keeps one bad past run from moving the
  baseline. Set `direction: lower_is_better` for latency, cost, or error counts.
- **`mad_outlier`**: the mean may not sit more than `k` (default 3) scaled MADs from the
  median of recent runs. The limit comes from how much the metric really moves between runs
  (MAD × 1.4826 estimates the standard deviation, without letting one wild run inflate it),
  so a noisy metric gets a wide band and a steady one a narrow band. `direction` is
  `higher_is_better` (default), `lower_is_better` or `both`; `both` also catches suspicious
  jumps the "good" way, such as a latency that halved because the run skipped work. If every
  past run gave the same value, any change the wrong way fails.
- **`pass_rate`**: for repeated trials, where each result is `1` (passed) or `0` (failed).
  The rule fails only when the whole Wilson score interval (`confidence`, default 0.95) of
  the pass rate is below `min`. So 7/10 against `min: 0.8` (interval 0.40 to 0.89) passes
  with a note, while 3/10 (0.11 to 0.60) fails. More trials narrow the interval and make
  the rule stricter. `scope: case` judges each case's trials on their own.

Every rule takes `required: true|false` (default `true`). Optional rules are reported but never
change the verdict.

### Expected cases

A crashed test often writes no result at all, so it quietly drops out of the average and
the run looks green. List the cases every run must report, and any that are missing make the
run **INCONCLUSIVE**:

```yaml
expected_cases: [refund-policy, shipping-faq, escalation]   # or: expected_cases: cases.txt
```

A file lists one case per line; blank lines and `#` comments are ignored. A case also
counts as missing if it ran but dropped a metric that a required rule reads. Errored and
skipped results count as reported, because the rules already handle them.

## Roadmap

- [x] JSONL + JUnit adapters, SQLite baseline store, `absolute` + `relative_delta`, exit-code contract
- [x] `mad_outlier` and `pass_rate` (Wilson interval over repeated trials)
- [x] Expected-case manifest: missing cases become INCONCLUSIVE
- [x] promptfoo and pytest-json-report adapters, PR-comment GitHub Action
- [ ] DeepEval adapter
- [x] Companion: `steadygate serve` with HTTP API, process sampling, dashboard and baseline suggestions
- [ ] Companion collectors for Android (adb), web (JS snippet) and iOS; LAN mode with pairing
- [ ] `steadygate flaky`

## Development

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev,companion]"
pytest && ruff check . && mypy
```

To build the demo site locally:

```bash
python -m build --wheel --outdir dist && python site/build.py --out _site --wheel dist/*.whl
```

Local-first: Steadygate makes no network calls and collects no telemetry. The companion
dashboard ships its own copy of Chart.js (MIT), so it works offline.

## License

MIT
