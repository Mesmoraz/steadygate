"use strict";

// Live demo UI. Stage 1 renders the precomputed scenarios from data/scenarios.json.
// Uses globals from knobs.js (knobsFromResults, runMeans) and markdown.js
// (renderMarkdown, escapeHtml). Every piece of data placed in the DOM goes through
// escapeHtml or textContent.
//
// Stage 2 loads Pyodide in the background, installs the Steadygate wheel built from the
// same commit, and turns on the knobs: every change re-runs the real engine through
// demo.py's run_json. `state` holds the UI state and `render(output)` draws the output
// panel from any engine result, {report, markdown} or {error}.

const state = { suite: "support", scenarioId: "noise", knobs: null, live: false };

let site = null; // parsed scenarios.json
let showRawMarkdown = false;
let runJson = null; // demo.py's run_json, once the engine is loaded
let runTimer = null;

const PYODIDE_INDEX_URL = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
const RUN_DEBOUNCE_MS = 150;

const STATUS = {
  loading: "Loading the Python engine…",
  ready: "Python engine ready: every change re-runs the real Steadygate code",
  failed: "Live mode couldn't load in this browser; scenarios still show real results.",
};

const DEMO_FALLBACK_URL = "https://github.com/Mesmoraz/steadygate#try-the-example";

const VERDICTS = {
  pass: {
    text: "PASS",
    icon: "✅",
    tone: "is-pass",
    summary: "Within normal variation of the baseline.",
  },
  regression: {
    text: "REGRESSION",
    icon: "❌",
    tone: "is-fail",
    summary: "A required rule failed: this is worse, not noise.",
  },
  inconclusive: {
    text: "INCONCLUSIVE",
    icon: "⚠️",
    tone: "is-warn",
    summary: "Results errored or were missing, so the gate refuses to call it a pass.",
  },
  config_error: {
    text: "CONFIG ERROR",
    icon: "⛔",
    tone: "is-fail",
    summary: "The config or results couldn't be read.",
  },
};

const OUTCOMES = {
  pass: { text: "pass", icon: "✅", tone: "is-pass" },
  fail: { text: "fail", icon: "❌", tone: "is-fail" },
  inconclusive: { text: "inconclusive", icon: "⚠️", tone: "is-warn" },
  no_baseline: { text: "no baseline", icon: "•", tone: "is-muted" },
};

function $(id) {
  return document.getElementById(id);
}

function esc(value) {
  return escapeHtml(String(value));
}

function fmt(v) {
  return String(Number(v.toFixed(3)));
}

function currentSuite() {
  return site.suites[state.suite];
}

function currentScenario() {
  const suite = currentSuite();
  return suite.scenarios.find((s) => s.id === state.scenarioId) || suite.scenarios[0];
}

// The example configs list expected cases inline: `expected_cases: [a, b, c]`.
function expectedCases(configYaml) {
  const m = /^expected_cases:\s*\[([^\]]*)\]/m.exec(configYaml);
  if (!m) return [];
  return m[1]
    .split(",")
    .map((s) => s.trim().replace(/^["']|["']$/g, ""))
    .filter(Boolean);
}

// ---------------------------------------------------------------- pickers

function renderSuiteTabs() {
  $("suite-tabs").innerHTML = Object.keys(site.suites)
    .map(
      (key) =>
        `<button type="button" class="suite-tab" data-suite="${esc(key)}" aria-pressed="${
          key === state.suite
        }">${esc(site.suites[key].label)}</button>`
    )
    .join("");
}

function renderScenarioButtons() {
  const suite = currentSuite();
  $("scenarios").innerHTML = suite.scenarios
    .map(
      (sc) =>
        `<button type="button" class="scenario" data-scenario="${esc(sc.id)}" aria-pressed="${
          sc.id === state.scenarioId
        }"><span class="scenario-label">${esc(sc.label)}</span>` +
        `<span class="scenario-caption">${esc(sc.caption)}</span></button>`
    )
    .join("");
}

// Updates the pressed state in place so keyboard focus stays on the clicked button.
function markPressed() {
  for (const b of document.querySelectorAll("[data-suite]")) {
    b.setAttribute("aria-pressed", String(b.dataset.suite === state.suite));
  }
  for (const b of document.querySelectorAll("[data-scenario]")) {
    b.setAttribute("aria-pressed", String(b.dataset.scenario === state.scenarioId));
  }
}

function selectSuite(key) {
  if (!site.suites[key] || key === state.suite) return;
  state.suite = key;
  state.scenarioId = site.suites[key].scenarios[0].id;
  renderScenarioButtons();
  selectScenario(state.scenarioId);
}

function selectScenario(id) {
  clearTimeout(runTimer);
  state.scenarioId = id;
  const suite = currentSuite();
  const sc = currentScenario();
  state.scenarioId = sc.id;
  state.knobs = knobsFromResults(state.suite, sc.results, expectedCases(suite.config));
  markPressed();
  renderKnobs();
  render({ report: sc.report, markdown: sc.markdown });
}

// ---------------------------------------------------------------- knobs

function knobDisabled(extra) {
  return !state.live || extra ? " disabled" : "";
}

function supportKnobsHtml(knobs) {
  const rows = Object.keys(knobs.cases).map((c, i) => {
    const k = knobs.cases[c];
    const acc = k.accuracy.toFixed(2);
    return (
      `<div class="knob-case${k.dropped ? " is-dropped" : ""}" data-case="${esc(c)}">` +
      `<div class="knob-head"><label class="knob-name" for="knob-acc-${i}">${esc(c)}` +
      `<span class="visually-hidden"> accuracy</span></label>` +
      `<span class="knob-value" data-value="accuracy">${esc(acc)}</span>` +
      `<span class="knob-toggles">` +
      `<button type="button" class="toggle" data-toggle="crashed" aria-pressed="${k.crashed}"` +
      `${knobDisabled(k.dropped)}>Crash<span class="visually-hidden"> ${esc(c)}</span></button>` +
      `<button type="button" class="toggle" data-toggle="dropped" aria-pressed="${k.dropped}"` +
      `${knobDisabled(false)}>Drop<span class="visually-hidden"> ${esc(c)}</span></button>` +
      `</span></div>` +
      `<input type="range" id="knob-acc-${i}" data-knob="accuracy" min="0" max="1" step="0.05" ` +
      `value="${esc(acc)}" aria-valuetext="${esc(acc)}"${knobDisabled(k.dropped || k.crashed)}>` +
      `</div>`
    );
  });
  const lat = knobs.latencyFactor.toFixed(1) + "×";
  return (
    `<div class="knob-cases">${rows.join("")}</div>` +
    `<div class="knob-case knob-latency"><div class="knob-head">` +
    `<label class="knob-name" for="knob-latency">Latency, all cases</label>` +
    `<span class="knob-value" data-value="latency">${esc(lat)}</span></div>` +
    `<input type="range" id="knob-latency" data-knob="latency" min="0.5" max="2" step="0.1" ` +
    `value="${esc(knobs.latencyFactor)}" aria-valuetext="${esc(lat)}"${knobDisabled(false)}>` +
    `<p class="knob-hint">Latency has an optional rule: it can fail without changing the ` +
    `verdict.</p></div>`
  );
}

function trialsKnobsHtml(knobs) {
  const rows = Object.keys(knobs.cases).map((c, i) => {
    const passes = knobs.cases[c].passes;
    return (
      `<div class="knob-case knob-stepper" data-case="${esc(c)}">` +
      `<span class="knob-name" id="knob-name-${i}">${esc(c)}</span>` +
      `<div class="stepper" role="group" aria-labelledby="knob-name-${i}">` +
      `<button type="button" class="step-btn" data-step="-1" aria-label="Fewer passes for ` +
      `${esc(c)}"${knobDisabled(false)}${passes <= 0 ? ' aria-disabled="true"' : ""}>` +
      `<span aria-hidden="true">−</span></button>` +
      `<span class="stepper-value"><strong data-value="passes">${esc(passes)}</strong>` +
      ` / 10 passes</span>` +
      `<button type="button" class="step-btn" data-step="1" aria-label="More passes for ` +
      `${esc(c)}"${knobDisabled(false)}${passes >= 10 ? ' aria-disabled="true"' : ""}>` +
      `<span aria-hidden="true">+</span></button></div></div>`
    );
  });
  return `<div class="knob-cases">${rows.join("")}</div>`;
}

function renderKnobs() {
  const box = $("knobs");
  if (!box || !state.knobs) return;
  box.innerHTML =
    state.suite === "trials" ? trialsKnobsHtml(state.knobs) : supportKnobsHtml(state.knobs);
}

// Updates one case's controls in place, so keyboard focus stays where it is.
function syncCase(el) {
  const k = state.knobs.cases[el.dataset.case];
  if (state.suite === "trials") {
    el.querySelector('[data-value="passes"]').textContent = String(k.passes);
    for (const b of el.querySelectorAll("[data-step]")) {
      const atEnd = b.dataset.step === "-1" ? k.passes <= 0 : k.passes >= 10;
      if (atEnd) b.setAttribute("aria-disabled", "true");
      else b.removeAttribute("aria-disabled");
    }
    return;
  }
  const acc = k.accuracy.toFixed(2);
  el.classList.toggle("is-dropped", k.dropped);
  el.querySelector('[data-value="accuracy"]').textContent = acc;
  const slider = el.querySelector('[data-knob="accuracy"]');
  slider.setAttribute("aria-valuetext", acc);
  slider.disabled = k.dropped || k.crashed;
  const crash = el.querySelector('[data-toggle="crashed"]');
  crash.setAttribute("aria-pressed", String(k.crashed));
  crash.disabled = k.dropped;
  el.querySelector('[data-toggle="dropped"]').setAttribute("aria-pressed", String(k.dropped));
}

function onKnobInput(e) {
  const input = e.target.closest("input[data-knob]");
  if (!input || !state.live) return;
  const value = Number(input.value);
  if (input.dataset.knob === "latency") {
    state.knobs.latencyFactor = value;
    const text = value.toFixed(1) + "×";
    $("knobs").querySelector('[data-value="latency"]').textContent = text;
    input.setAttribute("aria-valuetext", text);
  } else {
    const el = input.closest("[data-case]");
    state.knobs.cases[el.dataset.case].accuracy = value;
    syncCase(el);
  }
  knobsChanged();
}

function onKnobClick(e) {
  const btn = e.target.closest("button");
  if (!btn || !state.live || btn.disabled || btn.getAttribute("aria-disabled") === "true") return;
  const el = btn.closest("[data-case]");
  if (!el) return;
  const k = state.knobs.cases[el.dataset.case];
  if (btn.dataset.toggle) {
    k[btn.dataset.toggle] = !k[btn.dataset.toggle];
  } else if (btn.dataset.step) {
    k.passes = Math.min(10, Math.max(0, k.passes + Number(btn.dataset.step)));
  } else {
    return;
  }
  syncCase(el);
  knobsChanged();
}

// The knobs no longer match a scenario, so no scenario button stays highlighted.
function knobsChanged() {
  for (const b of document.querySelectorAll("[data-scenario]")) {
    b.setAttribute("aria-pressed", "false");
  }
  clearTimeout(runTimer);
  runTimer = setTimeout(runEngine, RUN_DEBOUNCE_MS);
}

function runEngine() {
  if (!runJson) return;
  const suite = currentSuite();
  let out;
  try {
    const payload = JSON.stringify({
      results: resultsFromKnobs(state.suite, state.knobs),
      config: suite.config,
      baselines: suite.baselines,
    });
    out = JSON.parse(runJson(payload));
  } catch (err) {
    out = { error: String((err && err.message) || err) };
  }
  render(out);
}

// ---------------------------------------------------------------- engine

function setStatus(key) {
  const note = $("engine-status");
  if (!note) return;
  note.dataset.state = key;
  note.textContent = STATUS[key];
}

// Deferred scripts (pyodide.js) have run by DOMContentLoaded, whether they loaded or not.
// readyState is already "interactive" before they run, so listen from the start instead.
const domReady = new Promise((resolve) => {
  if (document.readyState === "complete") resolve();
  else document.addEventListener("DOMContentLoaded", resolve, { once: true });
});

async function loadEngine() {
  setStatus("loading");
  try {
    await domReady;
    if (typeof loadPyodide !== "function") throw new Error("pyodide.js did not load");
    const pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX_URL });
    // Pyodide unvendors sqlite3 from the standard library; the history store needs it.
    await pyodide.loadPackage(["micropip", "sqlite3"]);
    const res = await fetch("wheels/latest.json");
    if (!res.ok) throw new Error("wheels/latest.json: HTTP " + res.status);
    const { wheel } = await res.json();
    const micropip = pyodide.pyimport("micropip");
    // PyYAML, the wheel's one dependency, comes from Pyodide's own package set.
    await micropip.install(new URL("wheels/" + wheel, location.href).href);
    const src = await fetch("demo.py");
    if (!src.ok) throw new Error("demo.py: HTTP " + src.status);
    pyodide.runPython(await src.text());
    runJson = pyodide.globals.get("run_json");
  } catch (err) {
    console.warn("Steadygate demo: live mode unavailable", err);
    setStatus("failed");
    return;
  }
  state.live = true;
  renderKnobs();
  setStatus("ready");
}

// ---------------------------------------------------------------- output panel

function badgeHtml(report) {
  const v = VERDICTS[report.verdict] || VERDICTS.config_error;
  return (
    `<div class="badge ${v.tone}">` +
    `<span class="badge-icon" aria-hidden="true">${v.icon}</span>` +
    `<span class="badge-text">${esc(v.text)}</span>` +
    `<span class="badge-exit">exit ${esc(report.exit_code)}</span>` +
    `<p class="badge-summary">${esc(v.summary)}</p></div>`
  );
}

function rulesHtml(report) {
  const rows = report.rules
    .map((r) => {
      const o = OUTCOMES[r.outcome] || { text: r.outcome, icon: "•", tone: "is-muted" };
      return (
        `<tr><td class="outcome ${o.tone}"><span aria-hidden="true">${o.icon}</span> ${esc(
          o.text
        )}</td>` +
        `<td><code>${esc(r.rule).replace(/:/g, ":<wbr>")}</code></td>` +
        `<td class="req">${r.required ? "yes" : "no"}</td>` +
        `<td class="detail">${esc(r.detail)}</td></tr>`
      );
    })
    .join("");
  return (
    `<section class="panel"><h3 class="panel-title">Rules</h3><div class="table-scroll">` +
    `<table class="rules"><thead><tr><th>Outcome</th><th>Rule</th><th>Required</th>` +
    `<th>Detail</th></tr></thead><tbody>${rows}</tbody></table></div></section>`
  );
}

function median(values) {
  const s = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

function historyHtml(report) {
  const suite = currentSuite();
  if (!suite.chart || !suite.baselines.length) return "";
  const metric = suite.chart.metric;
  const means = runMeans(suite.baselines, metric).filter((v) => Number.isFinite(v));
  if (!means.length) return "";
  const rule = report.rules.find((r) => r.rule === "relative_delta:" + metric);
  const med = rule && typeof rule.baseline === "number" ? rule.baseline : median(means);
  const limit = med * (1 - suite.chart.max_regression);
  const cand = rule && typeof rule.observed === "number" ? rule.observed : null;

  const all = means.concat([med, limit]).concat(cand === null ? [] : [cand]);
  let lo = Math.min.apply(null, all);
  let hi = Math.max.apply(null, all);
  const pad = Math.max((hi - lo) * 0.2, 0.01);
  lo -= pad;
  hi += pad;

  const W = 340;
  const top = 12;
  const bottom = 104;
  const y = (v) => top + ((hi - v) / (hi - lo)) * (bottom - top);
  const xs = means.map((_, i) => 36 + i * 60);
  const xCand = 36 + means.length * 60 + 20;
  const lineEnd = xCand + 22;
  const pct = Math.round(suite.chart.max_regression * 100);

  let svg =
    `<svg viewBox="0 0 ${W} 132" role="img" aria-label="${esc(
      `Baseline mean ${metric} per run: ${means.map(fmt).join(", ")}. Median ${fmt(med)}. ` +
        `Tolerance line ${fmt(limit)}.` +
        (cand === null ? " This run has no mean." : ` This run: ${fmt(cand)}.`)
    )}">` +
    `<line class="axis" x1="12" y1="${bottom + 6}" x2="${lineEnd}" y2="${bottom + 6}"/>` +
    `<line class="median" x1="12" y1="${y(med)}" x2="${lineEnd}" y2="${y(med)}"/>` +
    `<line class="tolerance" x1="12" y1="${y(limit)}" x2="${lineEnd}" y2="${y(limit)}"/>` +
    `<text class="label-median" x="${lineEnd + 6}" y="${y(med) + 4}">median ${esc(
      fmt(med)
    )}</text>` +
    `<text x="${lineEnd + 6}" y="${y(limit) + 4}">−${pct}% ${esc(fmt(limit))}</text>`;
  means.forEach((m, i) => {
    svg +=
      `<circle class="base-dot" cx="${xs[i]}" cy="${y(m)}" r="5"/>` +
      `<text x="${xs[i]}" y="126" text-anchor="middle">run ${i + 1}</text>`;
  });
  if (cand !== null) {
    const tone = (OUTCOMES[rule.outcome] || { tone: "is-muted" }).tone;
    svg += `<circle class="cand-dot ${tone}" cx="${xCand}" cy="${y(cand)}" r="7"/>`;
  }
  svg += `<text class="label-cand" x="${xCand}" y="126" text-anchor="middle">this run</text></svg>`;

  const note =
    cand === null
      ? `No mean ${esc(metric)} for this run.`
      : `This run's mean ${esc(metric)} is ${esc(fmt(cand))}; it fails below ${esc(
          fmt(limit)
        )}, ${pct}% under the median of the last ${means.length} runs.`;
  return (
    `<section class="panel history"><h3 class="panel-title">Compared against history</h3>` +
    `${svg}<p class="history-note">${note}</p></section>`
  );
}

function prCardHtml(markdown) {
  return (
    `<section class="pr" aria-label="Pull request comment preview">` +
    `<span class="pr-avatar" aria-hidden="true">S</span>` +
    `<div class="pr-card"><div class="pr-head"><span><span class="who">steadygate</span>` +
    `<span class="bot">bot</span> <span class="when">commented on your pull request</span>` +
    `</span><button type="button" class="md-toggle" id="md-toggle" aria-controls="md-raw" ` +
    `aria-expanded="${showRawMarkdown}">${showRawMarkdown ? "Hide" : "Show"} markdown</button>` +
    `</div><div class="pr-body">${renderMarkdown(markdown)}</div>` +
    `<pre class="md-raw" id="md-raw"${showRawMarkdown ? "" : " hidden"}></pre></div></section>`
  );
}

function render(output) {
  const panel = $("output");
  if (!panel) return;
  let errorBox = panel.querySelector(".engine-error");
  let body = panel.querySelector(".output-body");

  if (output.error !== undefined) {
    if (!errorBox) {
      errorBox = document.createElement("div");
      errorBox.className = "engine-error";
      errorBox.setAttribute("role", "alert");
      panel.prepend(errorBox);
    }
    errorBox.innerHTML = `<strong>The engine raised an error:</strong><pre></pre>`;
    errorBox.querySelector("pre").textContent = String(output.error);
    if (body) body.classList.add("is-stale");
    const loading = panel.querySelector(".loading");
    if (loading) loading.remove();
    return;
  }

  const { report, markdown } = output;
  panel.innerHTML =
    `<div class="output-body">${badgeHtml(report)}${historyHtml(report)}${rulesHtml(report)}` +
    `${prCardHtml(markdown)}</div>`;
  $("md-raw").textContent = markdown;
  $("md-toggle").addEventListener("click", toggleRawMarkdown);
  announce(report);
}

// One short line for screen readers; the output panel itself is not a live region.
function announce(report) {
  const live = $("verdict-live");
  if (!live) return;
  const v = VERDICTS[report.verdict] || VERDICTS.config_error;
  live.textContent = `${v.text}, exit ${report.exit_code}`;
}

function toggleRawMarkdown() {
  showRawMarkdown = !showRawMarkdown;
  const btn = $("md-toggle");
  btn.setAttribute("aria-expanded", String(showRawMarkdown));
  btn.textContent = (showRawMarkdown ? "Hide" : "Show") + " markdown";
  $("md-raw").hidden = !showRawMarkdown;
}

// ---------------------------------------------------------------- start-up

function showLoadError() {
  const demo = $("demo");
  if (!demo) return;
  demo.innerHTML =
    `<div class="wrap"><h2 id="demo-title">Try it</h2><p class="demo-error">` +
    `The demo couldn't load. See the example in the README.</p>` +
    `<p><a class="btn" href="${DEMO_FALLBACK_URL}">Open the README example</a></p></div>`;
}

async function init() {
  try {
    const res = await fetch("data/scenarios.json");
    if (!res.ok) throw new Error("HTTP " + res.status);
    site = await res.json();
    if (!site.suites || !site.suites[state.suite]) throw new Error("no default suite");
  } catch (err) {
    console.warn("Steadygate demo: scenarios failed to load", err);
    showLoadError();
    return;
  }

  $("suite-tabs").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-suite]");
    if (btn) selectSuite(btn.dataset.suite);
  });
  $("scenarios").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-scenario]");
    if (btn) selectScenario(btn.dataset.scenario);
  });

  $("knobs").addEventListener("input", onKnobInput);
  $("knobs").addEventListener("click", onKnobClick);

  renderSuiteTabs();
  renderScenarioButtons();
  selectScenario(state.scenarioId);
  loadEngine();
}

init();
