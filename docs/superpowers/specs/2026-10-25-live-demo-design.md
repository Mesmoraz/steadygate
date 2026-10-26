# Steadygate Live Demo and Walkthrough — Design

**Date:** 2026-10-25
**Status:** Draft, awaiting review

## 1. Intent

People who find the repo (mostly from LinkedIn: recruiters, hiring managers, engineers at
eval and QA teams) should be able to **see Steadygate working without installing
anything**, and understand the flow in about a minute, on a phone or a laptop.

Success: in about 60 seconds a visitor gets the core idea. Noise passes, a real regression
fails, and a crashed or missing case is INCONCLUSIVE, never a false green. They've also seen
the PR comment their team would get.

**Said by the user:** a demo you can open from GitHub; a walkthrough that makes the flow
clear; chosen forms are a **live page** and a **walkthrough**, with the walkthrough on the
demo site; the live page has **guided scenarios plus knobs**.
**Assumed (accepted in design review):** hosted free on GitHub Pages; the demo runs the
real Python code, not a JavaScript copy; first view must be fast on mobile.

**Out of scope:** uploading your own results file, video or GIF recording, the mkdocs docs
site (week 5), and demo PRs.

## 2. Approach

**Chosen:** a hand-written static site in `site/` (HTML, CSS, vanilla JS, no build step)
that loads [Pyodide](https://pyodide.org) from the jsDelivr CDN and installs the
Steadygate wheel built from the same commit. A GitHub Actions workflow builds and deploys it
to GitHub Pages on each push to `main`.

**Rejected:**
- **mkdocs with the demo embedded.** This would pull the week 5 docs tooling in early and
  tie the demo to it. The docs site can link to the demo later.
- **Reimplementing the rules in JavaScript.** It would load faster, but it isn't the real
  engine and would drift from it.

## 3. Page structure

One page, `index.html`, top to bottom:

1. **Hero.** "Your eval passed. Your agent got worse." One sentence on what Steadygate is,
   plus the three verdicts with their exit codes (0 / 1 / 2). Buttons: *Try it* (jumps to
   the demo) and *View on GitHub*.
2. **How it works.** Five short steps, each with a small inline-SVG diagram and two or
   three sentences:
   1. *Your eval tool runs:* promptfoo, pytest, JUnit, JSONL.
   2. *An adapter normalises results:* each one becomes a `case · metric · value · status`
      row; crashes become `error`.
   3. *History is the baseline:* passing runs from `main` are recorded and compared against
      the median of recent runs, not a fixed number.
   4. *Rules decide:* `relative_delta`, `absolute`, `mad_outlier`, `pass_rate`; required
      vs optional.
   5. *One verdict, one exit code, one PR comment:* PASS / REGRESSION / INCONCLUSIVE.
3. **Live demo** (section 4), placed after step 3 so the reader has just seen "history".
4. **Add it to your repo.** The minimal `uses: Mesmoraz/steadygate@main` workflow snippet
   and links to the README's Action section and quick start.
5. **Footer.** Repo link, MIT licence, "runs entirely in your browser, nothing is sent
   anywhere."

The layout works from 360 px wide up. It follows the system light/dark theme using CSS
custom properties, and verdict colours always come with a text label.

## 4. Live demo

### 4.1 Suites

The demo has two tabs, both using the repo's own example data:

| Tab | Data | Config |
|---|---|---|
| **Support agent** (default) | `examples/basic`: 5 cases, `accuracy` + `latency_ms`, 3 baseline runs | `examples/basic/steadygate.yml` |
| **Repeated trials** | `examples/trials`: 3 cases × 10 trials of `passed` | `examples/trials/steadygate.yml` |

### 4.2 Scenario buttons

| Tab | Scenario | Source file | Expected verdict |
|---|---|---|---|
| Support agent | Normal noise | `candidate-noise.jsonl` | PASS (0) |
| Support agent | Real regression | `candidate-regression.jsonl` | REGRESSION (1) |
| Support agent | Judge timed out | `candidate-broken.jsonl` | INCONCLUSIVE (2) |
| Support agent | Case never reported | `candidate-missing.jsonl` | INCONCLUSIVE (2) |
| Repeated trials | 7/10 is noise | `examples/trials/candidate-noise.jsonl` | PASS (0) |
| Repeated trials | 3/10 is real | `examples/trials/candidate-broken.jsonl` | REGRESSION (1) |

Each button has a one-line caption saying what to notice, for example "−2.2% is within the
5% tolerance".

### 4.3 Knobs

Picking a scenario loads its values into the knobs. Changing any knob re-runs the gate,
debounced to about 150 ms.

- **Support agent:** each case has an accuracy slider (0–1, step 0.05), a **Crash**
  toggle (the result becomes `status: error` with "judge timed out") and a **Drop**
  toggle (the case is left out of the results). There is also one latency multiplier
  slider (0.5×–2×), so visitors can see an optional rule fail without changing the
  verdict.
- **Repeated trials:** each case has a "passes out of 10" stepper (0–10).

### 4.4 Output panel

- **Verdict badge:** large text (PASS / REGRESSION / INCONCLUSIVE) with the exit code, an
  icon and a colour.
- **Rule table:** the rows from `report.as_dict()`: outcome, rule, required, detail.
- **History strip** (support agent only): mean accuracy of the three baseline runs and
  their median, with the candidate's mean and the −5% tolerance line on a small inline-SVG
  chart. The point is to show "compared against history".
- **PR comment preview:** the output of `report.markdown()`, rendered as a GitHub-style
  comment card. Rendering covers only what that function emits: a heading, a table,
  `<details>`, `<sub>` and inline code. It uses a small hand-written renderer, with all
  text HTML-escaped before markup is applied. There is a "Show markdown" toggle for the
  raw text.

### 4.5 Two-stage loading

Pyodide is about 10 MB and takes a few seconds to start, which is too slow for a first look
on a phone. So:

- **Stage 1 (instant).** At build time the Pages workflow runs the real CLI
  (`steadygate run --json` and `--markdown`) on every scenario in §4.2 and writes
  `site/data/scenarios.json` with the results, the report JSON and the markdown. Scenario
  buttons render from this file with no Python.
- **Stage 2 (background).** The page starts loading Pyodide straight away. Until it's ready
  the knobs are disabled, with the note "Loading the Python engine…". Once it's ready the
  note reads "Python engine ready: every change re-runs the real Steadygate code", and the
  knobs turn on.
- **Failure.** If Pyodide or the wheel fails to load, scenarios keep working and the knobs
  stay disabled, with "Live mode couldn't load in this browser; scenarios still show real
  results."

## 5. In-browser engine

`site/demo.py` is a pure module with no Pyodide imports, so CPython can test it:

```python
def run(suite: str, results: list[dict], config_yaml: str, baselines: list[list[dict]]) -> dict:
    """Gate `results` against `baselines` using the real engine.

    Returns {"report": report.as_dict(...), "markdown": report.markdown(...)}.
    """
```

- Results come in as dicts (`case`, `metric`, `value`, `status`, `message`) and become
  `steadygate.models.Result`s.
- The config comes from `config.parse(yaml.safe_load(config_yaml))`, overriding `store` to
  an in-memory SQLite database. For `expected_cases` the example configs use inline lists,
  so no file is read.
- Each baseline run is recorded with `Store.record(...)`, then `gate.evaluate(...)` runs,
  and the `report` module formats the output.
- No library changes are planned. If `Store` can't open `":memory:"` as written, the
  demo writes to a path in Pyodide's in-memory filesystem.

`app.js` owns the UI state. It builds the results list from the knobs, calls
`pyodide.globals.get("run")` with JSON-compatible values, and renders what comes back. The
baseline data and config text sit in `scenarios.json` (stage 1) and are passed to `run`
unchanged.

**Wheel delivery:** the workflow builds the wheel with `python -m build --wheel` and copies
it to `site/wheels/`, along with a `wheels/latest.json` that names the file. `app.js` reads
`latest.json` and calls `micropip.install(<wheel url>)`. PyYAML comes from Pyodide's own
package set via `micropip`.

**Pinned versions:** Pyodide is loaded from an exact version URL on cdn.jsdelivr.net. No
other third-party scripts are used.

## 6. Deployment

`.github/workflows/pages.yml`:

- Triggers: push to `main` touching `site/**`, `src/**`, `examples/**` or the workflow
  itself, plus `workflow_dispatch`.
- Permissions: `contents: read`, `pages: write`, `id-token: write`.
- Steps: checkout → setup-python → `pip install . build` → build the wheel → generate
  `scenarios.json` with `site/build_scenarios.py` (which **fails the job** if any scenario's
  exit code differs from §4.2) → `actions/upload-pages-artifact` → `actions/deploy-pages`.

One-time manual step for the user: **Settings → Pages → Source: GitHub Actions**.

README: a "▶ Live demo" link near the top and in the Quick start, pointing to
`https://mesmoraz.github.io/steadygate/`.

## 7. Files

| Path | Purpose |
|---|---|
| `site/index.html` | Page markup, walkthrough copy, inline SVG diagrams |
| `site/style.css` | Theme tokens (light/dark), layout, components |
| `site/app.js` | Scenario/knob state, Pyodide loading, rendering, markdown renderer |
| `site/demo.py` | Engine bridge: dicts → `Result`s → `gate.evaluate` → report dict + markdown |
| `site/build_scenarios.py` | Build-time: run the CLI per scenario, check exit codes, write `data/scenarios.json` |
| `.github/workflows/pages.yml` | Build wheel + scenarios, deploy to Pages |
| `tests/test_site_demo.py` | `demo.run` matches the CLI's verdicts for every scenario; knob edge cases |
| `README.md` | Live demo link |

`site/data/` and `site/wheels/` are build outputs and go in `.gitignore`.

## 8. Error handling

- A knob state the engine rejects (for example every case dropped) shows the engine's own
  answer, such as an INCONCLUSIVE badge for missing cases. The UI never makes up a
  verdict.
- A Python exception in `run` shows "The engine raised an error" plus the message in the
  output panel, and the last good result stays visible but dimmed.
- If `scenarios.json` fails to load, the page shows the walkthrough and a link to the
  README example instead of an empty demo.

## 9. Testing

- `tests/test_site_demo.py` (CPython, part of the normal `pytest` run):
  - For every scenario in §4.2, `demo.run` returns the same verdict and the same
    `report.as_dict()` as the CLI on the same files.
  - Knob edge cases: crashing every case → INCONCLUSIVE; dropping a case → INCONCLUSIVE;
    accuracy 0 on one case → REGRESSION (per-case floor); latency 2× → PASS with the
    optional rule failing.
- `build_scenarios.py` checks the exit codes again when the site is built.
- Manual check in the browser pane: stage 1 renders with Pyodide blocked; stage 2 knobs
  re-run; layout at 375 px and desktop; light and dark; no console errors.
- `ruff` and `mypy` cover `site/*.py` (add `site` to mypy `files`).

## 10. Risks

- **Pyodide size on mobile data.** This is handled by stage 1: the demo is useful before
  Python loads.
- **CDN or Pyodide version churn.** The version is pinned, and stage 1 doesn't depend on it.
- **The demo drifting from the code.** The wheel is built from the same commit, the
  scenarios are produced by the real CLI, and the tests compare `demo.run` with the CLI.
