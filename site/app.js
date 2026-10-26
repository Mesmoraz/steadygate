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
let lastResults = []; // the result rows behind the output on screen
let prOpen = false;
let showTech = false;

const PYODIDE_INDEX_URL = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
const RUN_DEBOUNCE_MS = 150;

const STATUS = {
  loading: "Starting the live checker in your browser (a few seconds)…",
  ready: "Live: each change re-runs the real Steadygate engine, right here in your browser.",
  failed: "Live mode couldn't start in this browser. The examples on the left still work.",
};

const DEMO_FALLBACK_URL = "https://github.com/Mesmoraz/steadygate#try-the-example";

// `plain` is what a visitor reads; `text` is the engine's own name, shown small.
const VERDICTS = {
  pass: {
    plain: "Safe to ship",
    text: "PASS",
    icon: "✅",
    tone: "is-pass",
    summary: "The scores are within the normal ups and downs of your recent releases.",
  },
  regression: {
    plain: "Don't ship: it got worse",
    text: "REGRESSION",
    icon: "❌",
    tone: "is-fail",
    summary: "A required check failed. This is a real drop, bigger than normal wobble.",
  },
  inconclusive: {
    plain: "Can't tell: the test itself broke",
    text: "INCONCLUSIVE",
    icon: "⚠️",
    tone: "is-warn",
    summary:
      "Part of the test crashed or never reported, so Steadygate won't call it safe. " +
      "Fix the test run and try again.",
  },
  config_error: {
    plain: "Couldn't check",
    text: "CONFIG ERROR",
    icon: "🛑",
    tone: "is-fail",
    summary: "The settings or the results file couldn't be read.",
  },
};

const OUTCOMES = {
  pass: { text: "Yes", icon: "✅", tone: "is-pass" },
  fail: { text: "No", icon: "❌", tone: "is-fail" },
  inconclusive: { text: "Can't tell", icon: "⚠️", tone: "is-warn" },
  no_baseline: { text: "No history yet", icon: "•", tone: "is-muted" },
};

// Readable names for the example cases.
const CASE_NAMES = {
  "refund-policy": "Refund question",
  "shipping-faq": "Shipping question",
  escalation: "“Let me talk to a human”",
  "order-status": "“Where's my order?”",
  "returns-window": "Return deadline question",
  "book-flight": "Book a flight",
  "cancel-order": "Cancel an order",
  "track-parcel": "Track a parcel",
};

const SUITE_TEXT = {
  support: {
    intro:
      "A support chatbot answers 5 customer questions, 10 times each. An AI grader marks " +
      "each answer right or wrong. Recent releases scored about 90%.",
    knobs:
      "Drag a question's score, or break part of the test, and see what Steadygate says.",
  },
  trials: {
    intro:
      "An AI agent tries 3 tasks, 10 times each; every try works or fails. The goal is for " +
      "each task to work at least 80% of the time.",
    knobs: "Change how many of the 10 tries worked, and see what Steadygate says.",
  },
};

function caseName(c) {
  return CASE_NAMES[c] || c;
}

function pct(v, digits = 0) {
  return (v * 100).toFixed(digits).replace(/\.0+$/, "") + "%";
}

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

function renderSuiteText() {
  const text = SUITE_TEXT[state.suite] || { intro: "", knobs: "" };
  $("suite-intro").textContent = text.intro;
  const lede = $("knobs-lede");
  if (lede) lede.textContent = text.knobs;
}

function renderScenarioButtons() {
  renderSuiteText();
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
  lastResults = sc.results;
  render({ report: sc.report, markdown: sc.markdown });
}

// ---------------------------------------------------------------- knobs

function knobDisabled(extra) {
  return !state.live || extra ? " disabled" : "";
}

// What the score readout shows for a case: its score, or why it has none.
function accuracyText(k) {
  if (k.dropped) return "no result";
  if (k.crashed) return "not graded";
  return pct(k.accuracy) + " right";
}

function latencyText(factor) {
  return factor === 1 ? "usual" : `${factor.toFixed(1)}× usual`;
}

function supportKnobsHtml(knobs) {
  const rows = Object.keys(knobs.cases).map((c, i) => {
    const k = knobs.cases[c];
    const name = caseName(c);
    const shown = accuracyText(k);
    return (
      `<div class="knob-case${k.dropped ? " is-dropped" : ""}" data-case="${esc(c)}">` +
      `<div class="knob-head"><label class="knob-name" for="knob-acc-${i}">${esc(name)}` +
      `<span class="visually-hidden"> score</span></label>` +
      `<span class="knob-value" data-value="accuracy">${esc(shown)}</span></div>` +
      `<input type="range" id="knob-acc-${i}" data-knob="accuracy" min="0" max="1" step="0.05" ` +
      `value="${esc(k.accuracy)}" aria-valuetext="${esc(shown)}"` +
      `${knobDisabled(k.dropped || k.crashed)}>` +
      `<span class="knob-toggles">` +
      `<button type="button" class="toggle" data-toggle="crashed" aria-pressed="${k.crashed}"` +
      `${knobDisabled(k.dropped)}>Grader crashed<span class="visually-hidden"> on ${esc(
        name
      )}</span></button>` +
      `<button type="button" class="toggle" data-toggle="dropped" aria-pressed="${k.dropped}"` +
      `${knobDisabled(false)}>Didn't run<span class="visually-hidden">: ${esc(
        name
      )}</span></button>` +
      `</span></div>`
    );
  });
  const lat = latencyText(knobs.latencyFactor);
  return (
    `<div class="knob-cases">${rows.join("")}</div>` +
    `<div class="knob-case knob-latency"><div class="knob-head">` +
    `<label class="knob-name" for="knob-latency">Response time</label>` +
    `<span class="knob-value" data-value="latency">${esc(lat)}</span></div>` +
    `<input type="range" id="knob-latency" data-knob="latency" min="0.5" max="2" step="0.1" ` +
    `value="${esc(knobs.latencyFactor)}" aria-valuetext="${esc(lat)}"${knobDisabled(false)}>` +
    `<p class="knob-hint">Tracked and reported, but a slower reply never blocks a release on ` +
    `its own.</p></div>`
  );
}

function trialsKnobsHtml(knobs) {
  const rows = Object.keys(knobs.cases).map((c, i) => {
    const passes = knobs.cases[c].passes;
    const name = caseName(c);
    return (
      `<div class="knob-case knob-stepper" data-case="${esc(c)}">` +
      `<span class="knob-name" id="knob-name-${i}">${esc(name)}</span>` +
      `<div class="stepper" role="group" aria-labelledby="knob-name-${i}">` +
      `<button type="button" class="step-btn" data-step="-1" aria-label="One fewer try worked ` +
      `for ${esc(name)}"${knobDisabled(false)}${passes <= 0 ? ' aria-disabled="true"' : ""}>` +
      `<span aria-hidden="true">−</span></button>` +
      `<span class="stepper-value"><strong data-value="passes">${esc(passes)}</strong>` +
      ` of 10 worked</span>` +
      `<button type="button" class="step-btn" data-step="1" aria-label="One more try worked ` +
      `for ${esc(name)}"${knobDisabled(false)}${passes >= 10 ? ' aria-disabled="true"' : ""}>` +
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
  const shown = accuracyText(k);
  el.classList.toggle("is-dropped", k.dropped);
  el.querySelector('[data-value="accuracy"]').textContent = shown;
  const slider = el.querySelector('[data-knob="accuracy"]');
  slider.setAttribute("aria-valuetext", shown);
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
    const text = latencyText(value);
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
    const results = resultsFromKnobs(state.suite, state.knobs);
    lastResults = results;
    const payload = JSON.stringify({
      results,
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
    `<span class="badge-text">${esc(v.plain)}</span>` +
    `<p class="badge-summary">${esc(v.summary)}</p>` +
    `<p class="badge-exit">Engine verdict: ${esc(v.text)} · CI exit code ${esc(
      report.exit_code
    )}</p></div>`
  );
}

// ---------------------------------------------------------------- plain-language checks
//
// Each check's question and answer are written from the real numbers: the rule outcome
// from the engine plus the result rows that were gated. Thresholds are read from the
// engine's own detail text, so the words can't drift from what the rule enforced.

function numberIn(text, re, fallback) {
  const m = re.exec(text || "");
  return m ? Number(m[1]) : fallback;
}

function listNames(names) {
  if (names.length <= 1) return names.join("");
  return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
}

function rowsFor(metric, status) {
  return lastResults.filter((r) => r.metric === metric && (r.status || "ok") === status);
}

function erroredNames(metric) {
  return listNames(rowsFor(metric, "error").map((r) => caseName(r.case)));
}

function change(obs, base, lower, higher) {
  const d = ((obs - base) / base) * 100;
  if (Math.abs(d) < 0.05) return "no change";
  return `${Math.abs(d).toFixed(1)}% ${d < 0 ? lower : higher}`;
}

function coverageCheck(r) {
  const unit = state.suite === "trials" ? "task" : "question";
  const expected = expectedCases(currentSuite().config);
  const reported = new Set(lastResults.map((x) => x.case));
  const missing = expected.filter((c) => !reported.has(c)).map(caseName);
  const q = `Did every ${unit} report a result?`;
  if (r.outcome === "pass") return [q, `Yes, all ${expected.length} ${unit}s reported.`];
  if (!missing.length) return [q, r.detail];
  return [
    q,
    `No. ${listNames(missing)} never reported anything, so the results are incomplete. A ` +
      `test that crashes silently looks like a pass in most tools.`,
  ];
}

function scoreCheck(r) {
  const limit = numberIn(r.detail, /limit ([\d.]+)% worse/, 5);
  const q = "Is the overall score close to your recent releases?";
  const obs = r.observed;
  const base = r.baseline;
  if (r.outcome === "no_baseline" || typeof base !== "number") {
    return [q, "Not enough past releases are recorded yet to compare against."];
  }
  if (typeof obs !== "number") return [q, "No overall score could be worked out for this run."];
  const vs = `${pct(obs, 1)} overall against the usual ${pct(base, 1)}`;
  const delta = change(obs, base, "lower", "higher");
  if (r.outcome === "pass") {
    return [q, `Yes. ${vs} (${delta}). Up to ${limit}% lower counts as normal wobble.`];
  }
  if (r.outcome === "fail") {
    return [q, `No. ${vs}: ${delta}, more than the ${limit}% normal wobble.`];
  }
  return [
    q,
    `${vs} looks fine, but ${erroredNames(r.metric) || "part of the test"} couldn't be ` +
      `graded, so this score can't be trusted.`,
  ];
}

function floorCheck(r) {
  const min = numberIn(r.detail, />=\s*([\d.]+)/, 0.5);
  const q = `Does every question score at least ${pct(min)}?`;
  const ok = rowsFor(r.metric, "ok").filter((x) => typeof x.value === "number");
  if (r.outcome === "fail") {
    const below = ok.filter((x) => x.value < min);
    const named = below.map((x) => `${caseName(x.case)} (${pct(x.value)})`);
    return [q, `No. ${listNames(named)} scored below ${pct(min)}, a hard minimum.`];
  }
  if (r.outcome === "inconclusive") {
    return [
      q,
      `The graded questions are all above ${pct(min)}, but ` +
        `${erroredNames(r.metric) || "part of the test"} couldn't be graded.`,
    ];
  }
  if (!ok.length) return [q, r.detail];
  const lowest = ok.reduce((a, b) => (b.value < a.value ? b : a));
  return [q, `Yes. The lowest was ${caseName(lowest.case)} at ${pct(lowest.value)}.`];
}

function latencyCheck(r) {
  const limit = numberIn(r.detail, /limit ([\d.]+)% worse/, 25);
  const q = "Is the response time normal?";
  const obs = r.observed;
  const base = r.baseline;
  if (typeof obs !== "number" || typeof base !== "number") return [q, r.detail];
  const vs = `Replies averaged ${Math.round(obs)} ms against the usual ${Math.round(base)} ms`;
  const delta = change(obs, base, "faster", "slower");
  if (r.outcome === "fail") {
    return [
      q,
      `No. ${vs}: ${delta}, more than the ${limit}% allowed. Worth a look, but this check ` +
        `never blocks a release on its own.`,
    ];
  }
  return [q, `Yes. ${vs} (${delta}). Up to ${limit}% slower is fine.`];
}

function passRateCheck(r) {
  const target = numberIn(r.detail, /target ([\d.]+)%/, 80);
  const q = `Does each task work often enough? (Goal: ${target}%, allowing for luck.)`;
  const tally = {};
  for (const x of rowsFor(r.metric, "ok")) {
    const t = (tally[x.case] = tally[x.case] || { k: 0, n: 0 });
    t.n += 1;
    if (x.value === 1) t.k += 1;
  }
  if (r.outcome === "fail") {
    const named = [];
    const re = /([\w-]+) (\d+)\/(\d+)/g;
    let m;
    while ((m = re.exec(r.detail.split(":").slice(1).join(":")))) {
      named.push(`${caseName(m[1])} worked only ${m[2]} of ${m[3]} times`);
    }
    if (!named.length) return [q, r.detail];
    return [q, `No. ${listNames(named)}. Even allowing for luck, that's clearly below ${target}%.`];
  }
  if (r.outcome !== "pass") return [q, r.detail];
  const cases = Object.keys(tally);
  if (!cases.length) return [q, r.detail];
  const weakest = cases.reduce((a, b) => (tally[b].k / tally[b].n < tally[a].k / tally[a].n ? b : a));
  const w = tally[weakest];
  if (w.k / w.n >= target / 100) {
    return [q, `Yes. Every task worked at least ${target}% of the time.`];
  }
  return [
    q,
    `Yes. The weakest, ${caseName(weakest)}, worked ${w.k} of ${w.n} times. With only ` +
      `${w.n} tries, a task that really works ${target}% of the time can do that by chance.`,
  ];
}

function plainCheck(r) {
  const kind = r.rule.split(":")[0];
  if (kind === "coverage") return coverageCheck(r);
  if (kind === "pass_rate") return passRateCheck(r);
  if (kind === "absolute" && r.metric === "accuracy") return floorCheck(r);
  if (kind === "relative_delta" && r.metric === "accuracy") return scoreCheck(r);
  if (kind === "relative_delta" && r.metric === "latency_ms") return latencyCheck(r);
  return [r.rule, r.detail];
}

function checksHtml(report) {
  const items = report.rules
    .map((r) => {
      const o = OUTCOMES[r.outcome] || { text: r.outcome, icon: "•", tone: "is-muted" };
      const [question, answer] = plainCheck(r);
      const optional = r.required ? "" : `<span class="check-opt">doesn't block a release</span>`;
      return (
        `<li class="check"><span class="check-mark ${o.tone}" role="img" aria-label="${esc(
          o.text
        )}">${o.icon}</span><div class="check-body">` +
        `<p class="check-q">${esc(question)}${optional}</p>` +
        `<p class="check-a">${esc(answer)}</p>` +
        `<p class="check-tech"><code>${esc(r.rule).replace(/:/g, ":<wbr>")}</code> ` +
        `${esc(r.detail)}</p></div></li>`
      );
    })
    .join("");
  return (
    `<section class="panel checks${showTech ? " show-tech" : ""}">` +
    `<div class="panel-head"><h3 class="panel-title">The checks behind the answer</h3>` +
    `<button type="button" class="tech-toggle" id="tech-toggle" aria-pressed="${showTech}">` +
    `Engine details</button></div><ul class="check-list">${items}</ul></section>`
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

  const show = (v) => (metric === "accuracy" ? pct(v, 1) : fmt(v));
  const W = 340;
  const top = 12;
  const bottom = 104;
  const y = (v) => top + ((hi - v) / (hi - lo)) * (bottom - top);
  const xs = means.map((_, i) => 36 + i * 60);
  const xCand = 36 + means.length * 60 + 20;
  const lineEnd = xCand + 22;
  const tol = Math.round(suite.chart.max_regression * 100);

  let svg =
    `<svg viewBox="0 0 ${W} 132" role="img" aria-label="${esc(
      `Overall score of each recent release: ${means.map(show).join(", ")}. Usual ` +
        `${show(med)}. Normal wobble reaches down to ${show(limit)}.` +
        (cand === null ? " This version has no score." : ` This version: ${show(cand)}.`)
    )}">` +
    `<line class="axis" x1="12" y1="${bottom + 6}" x2="${lineEnd}" y2="${bottom + 6}"/>` +
    `<line class="median" x1="12" y1="${y(med)}" x2="${lineEnd}" y2="${y(med)}"/>` +
    `<line class="tolerance" x1="12" y1="${y(limit)}" x2="${lineEnd}" y2="${y(limit)}"/>` +
    `<text class="label-median" x="${lineEnd + 6}" y="${y(med) + 4}">usual ${esc(
      show(med)
    )}</text>` +
    `<text x="${lineEnd + 6}" y="${y(limit) + 4}">−${tol}%: ${esc(show(limit))}</text>`;
  means.forEach((m, i) => {
    svg +=
      `<circle class="base-dot" cx="${xs[i]}" cy="${y(m)}" r="5"/>` +
      `<text x="${xs[i]}" y="126" text-anchor="middle">release ${i + 1}</text>`;
  });
  if (cand !== null) {
    const tone = (OUTCOMES[rule.outcome] || { tone: "is-muted" }).tone;
    svg += `<circle class="cand-dot ${tone}" cx="${xCand}" cy="${y(cand)}" r="7"/>`;
  }
  svg += `<text class="label-cand" x="${xCand}" y="126" text-anchor="middle">new</text></svg>`;

  let note;
  if (cand === null) {
    note = "This version has no overall score.";
  } else {
    note =
      `This version scored ${esc(show(cand))} overall. Anything above ${esc(show(limit))} ` +
      `(${tol}% below the usual ${esc(show(med))}) counts as normal wobble.`;
    if (rule.outcome === "inconclusive") {
      note += " But part of the test broke, so this number can't be trusted.";
    }
  }
  return (
    `<section class="panel history"><h3 class="panel-title">Overall score: recent releases ` +
    `vs the new version</h3>${svg}<p class="history-note">${note}</p></section>`
  );
}

function prCardHtml(markdown) {
  return (
    `<details class="pr-wrap" id="pr-wrap"${prOpen ? " open" : ""}><summary>What your team ` +
    `sees on the pull request</summary>` +
    `<section class="pr" aria-label="Pull request comment preview">` +
    `<span class="pr-avatar" aria-hidden="true">S</span>` +
    `<div class="pr-card"><div class="pr-head"><span><span class="who">steadygate</span>` +
    `<span class="bot">bot</span> <span class="when">commented on your pull request</span>` +
    `</span><button type="button" class="md-toggle" id="md-toggle" aria-controls="md-raw" ` +
    `aria-expanded="${showRawMarkdown}">${showRawMarkdown ? "Hide" : "Show"} markdown</button>` +
    `</div><div class="pr-body">${renderMarkdown(markdown)}</div>` +
    `<pre class="md-raw" id="md-raw"${showRawMarkdown ? "" : " hidden"}></pre></div></section>` +
    `</details>`
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
    `<div class="output-body">${badgeHtml(report)}${checksHtml(report)}` +
    `${historyHtml(report)}${prCardHtml(markdown)}</div>`;
  $("md-raw").textContent = markdown;
  $("md-toggle").addEventListener("click", toggleRawMarkdown);
  $("tech-toggle").addEventListener("click", toggleTech);
  $("pr-wrap").addEventListener("toggle", (e) => {
    prOpen = e.target.open;
  });
  announce(report);
}

// One short line for screen readers; the output panel itself is not a live region.
function announce(report) {
  const live = $("verdict-live");
  if (!live) return;
  const v = VERDICTS[report.verdict] || VERDICTS.config_error;
  live.textContent = `${v.plain}. ${v.text}, exit ${report.exit_code}`;
}

function toggleTech() {
  showTech = !showTech;
  $("tech-toggle").setAttribute("aria-pressed", String(showTech));
  document.querySelector(".checks").classList.toggle("show-tech", showTech);
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
