"use strict";

// Live demo UI. Stage 1 renders the precomputed scenarios from data/scenarios.json.
// Uses globals from knobs.js (knobsFromResults, runMeans) and markdown.js
// (renderMarkdown, escapeHtml). Every piece of data placed in the DOM goes through
// escapeHtml or textContent.
//
// For live mode (stage 2): `state` holds the UI state and `render(output)` draws the
// output panel from any engine result, {report, markdown} or {error}.

const state = { suite: "support", scenarioId: "noise", knobs: null, live: false };

let site = null; // parsed scenarios.json
let showRawMarkdown = false;

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
  state.scenarioId = id;
  const suite = currentSuite();
  const sc = currentScenario();
  state.scenarioId = sc.id;
  state.knobs = knobsFromResults(state.suite, sc.results, expectedCases(suite.config));
  markPressed();
  render({ report: sc.report, markdown: sc.markdown });
}

// ---------------------------------------------------------------- output panel

function badgeHtml(report) {
  const v = VERDICTS[report.verdict] || VERDICTS.config_error;
  return (
    `<div class="badge ${v.tone}" role="status">` +
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
    errorBox.innerHTML = `<strong>The engine raised an error</strong><pre></pre>`;
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

  renderSuiteTabs();
  renderScenarioButtons();
  selectScenario(state.scenarioId);
}

init();
