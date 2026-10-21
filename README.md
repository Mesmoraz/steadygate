# Steadygate

**A statistical release gate for non-deterministic systems: LLM agents, evals, and flaky test suites.**

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
```

## Input formats

| Format | Detected by | Produces |
|---|---|---|
| **JSONL** | `.jsonl`, `.ndjson` | `{"case", "metric", "value", "status"?, "message"?}` per line |
| **JUnit XML** | `.xml` | `passed` (1/0) and `duration_s` per test case; `<error>` is treated as a broken run, `<skipped>` is ignored |

JUnit covers pytest (`--junitxml`), Jest, Playwright, Vitest, Maven and most CI tools.
Adapters for promptfoo, DeepEval and pytest-json-report are on the roadmap. Use `--format` to
override detection.

## Rules

- **`absolute`**: `min` and/or `max` on the mean (`scope: aggregate`) or on every case
  (`scope: case`). Use it for deterministic contracts.
- **`relative_delta`**: the mean may not regress more than `max_regression` (a fraction)
  against the **median** of recent runs. The median keeps one bad past run from moving the
  baseline. Set `direction: lower_is_better` for latency, cost, or error counts.

Every rule takes `required: true|false` (default `true`). Optional rules are reported but never
change the verdict.

## Roadmap

- [x] JSONL + JUnit adapters, SQLite baseline store, `absolute` + `relative_delta`, exit-code contract
- [ ] `mad_outlier` and `pass_rate` (Wilson interval over repeated trials)
- [ ] Expected-case manifest: missing cases become INCONCLUSIVE
- [ ] promptfoo / pytest-json / DeepEval adapters, PR-comment GitHub Action
- [ ] `steadygate suggest` (propose thresholds from history) and `steadygate flaky`

## Development

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest && ruff check . && mypy
```

Local-first: Steadygate makes no network calls and collects no telemetry.

## License

MIT
