// Steadygate companion dashboard. Talks to the same HTTP API any app would use.
"use strict";

const $ = (id) => document.getElementById(id);
const state = { session: null, target: null, charts: {}, poll: null, suggested: null, lang: "curl" };
const STAT_KEYS = ["n", "mean", "median", "mad", "stdev", "min", "p25", "p75", "p95", "max"];

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const msg = typeof d === "string" ? d : d && d.message ? d.message : JSON.stringify(d);
    const err = new Error(msg || res.statusText);
    err.detail = d;
    throw err;
  }
  return data;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const c of children) node.append(c);
  return node;
}

function fmt(v) {
  if (v === null || v === undefined) return "–";
  if (Number.isInteger(v)) return String(v);
  return Math.abs(v) >= 100 ? v.toFixed(1) : v.toPrecision(4);
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// --- collectors ---------------------------------------------------------------------

async function loadCollectors() {
  const { collectors } = await api("GET", "collectors");
  $("collectors").replaceChildren(
    ...collectors.map((c) => {
      const what = c.metrics === "any" ? "any metric your app sends" : c.metrics.map((m) => m.name).join(", ");
      return el("li", { title: c.detail }, el("b", { textContent: c.id }), `: ${what}`);
    }),
  );
}

// --- new session --------------------------------------------------------------------

let searchTimer = null;
$("proc-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  state.target = null;
  const q = e.target.value.trim();
  if (!q) return $("proc-results").replaceChildren();
  searchTimer = setTimeout(async () => {
    const { processes } = await api("GET", `processes?q=${encodeURIComponent(q)}`);
    $("proc-results").replaceChildren(
      ...processes.slice(0, 20).map((p) => {
        const li = el("li", { textContent: `${p.name}  ·  pid ${p.pid}` });
        li.addEventListener("click", () => {
          state.target = p;
          $("proc-search").value = p.name;
          for (const x of $("proc-results").children) x.classList.remove("selected");
          li.classList.add("selected");
        });
        return li;
      }),
    );
  }, 250);
});

$("start").addEventListener("click", async () => {
  $("start-error").hidden = true;
  const collectors = state.target ? [{ id: "process", params: { pid: state.target.pid } }] : [];
  try {
    const s = await api("POST", "sessions", {
      name: $("session-name").value,
      collectors,
      interval_s: Number($("interval").value) || 1,
    });
    openSession(s);
  } catch (err) {
    const hints = err.detail && err.detail.suggestions && err.detail.suggestions.length
      ? ` Did you mean: ${err.detail.suggestions.join(", ")}?` : "";
    $("start-error").textContent = err.message + hints;
    $("start-error").hidden = false;
  }
});

function openSession(s) {
  state.session = s.id;
  for (const c of Object.values(state.charts)) c.chart.destroy();
  state.charts = {};
  $("charts").replaceChildren();
  $("verdict").hidden = true;
  $("outcomes").replaceChildren();
  $("suggest-yaml").hidden = $("apply").hidden = true;
  $("suggest-why").textContent = $("baseline-msg").textContent = $("runs").textContent = "";
  $("session-id").textContent = s.id;
  $("target").textContent = state.target
    ? `Sampling ${state.target.name} (pid ${state.target.pid}) and accepting pushed metrics.`
    : "Push-only session: send metrics from your app (see below).";
  for (const id of ["live", "stats-panel", "baseline-panel", "gate-panel", "snippets-panel"]) $(id).hidden = false;
  renderSnippet();
  clearInterval(state.poll);
  refresh();
  state.poll = setInterval(refresh, 1000);
}

// --- live view ----------------------------------------------------------------------

function chartFor(metric) {
  if (state.charts[metric]) return state.charts[metric].chart;
  const canvas = el("canvas");
  $("charts").append(el("div", { className: "chart" }, el("h3", { textContent: metric }), canvas));
  const chart = new Chart(canvas, {
    type: "line",
    data: { labels: [], datasets: [{ data: [], borderColor: cssVar("--accent"), borderWidth: 2, pointRadius: 0, tension: 0.2 }] },
    options: {
      animation: false,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: cssVar("--muted"), maxTicksLimit: 6 }, grid: { color: cssVar("--border") } },
        y: { ticks: { color: cssVar("--muted") }, grid: { color: cssVar("--border") } },
      },
    },
  });
  state.charts[metric] = { chart };
  return chart;
}

async function refresh() {
  if (!state.session) return;
  const s = await api("GET", `sessions/${state.session}`);
  $("session-state").textContent = s.state;
  $("stop").disabled = s.state !== "running";
  const byMetric = {};
  for (const r of s.results) {
    if (r.status !== "ok") continue;
    (byMetric[r.metric] ||= { labels: [], values: [] });
    byMetric[r.metric].labels.push(r.case);
    byMetric[r.metric].values.push(r.value);
  }
  for (const [metric, series] of Object.entries(byMetric)) {
    const chart = chartFor(metric);
    chart.data.labels = series.labels;
    chart.data.datasets[0].data = series.values;
    chart.update();
  }
  await renderStats();
  if (s.state !== "running") clearInterval(state.poll);
}

async function renderStats() {
  const { stats } = await api("GET", `sessions/${state.session}/stats`);
  const metrics = Object.keys(stats);
  const head = el("tr", {}, el("th", { textContent: "metric" }), ...STAT_KEYS.map((k) => el("th", { textContent: k })));
  const rows = metrics.map((m) =>
    el("tr", {}, el("td", { textContent: m }), ...STAT_KEYS.map((k) => el("td", { textContent: fmt(stats[m][k]) }))),
  );
  $("stats").replaceChildren(el("thead", {}, head), el("tbody", {}, ...rows));
  const select = $("suggest-metric");
  const current = select.value;
  select.replaceChildren(...metrics.map((m) => el("option", { value: m, textContent: m })));
  if (metrics.includes(current)) select.value = current;
}

$("stop").addEventListener("click", async () => {
  await api("POST", `sessions/${state.session}/stop`);
  refresh();
});

// --- baseline -----------------------------------------------------------------------

$("record").addEventListener("click", async () => {
  try {
    const r = await api("POST", `sessions/${state.session}/record`);
    $("runs").textContent = `Recorded ${r.recorded} values. ${r.runs} baseline run(s) in history.`;
  } catch (err) {
    $("runs").textContent = err.message;
  }
});

$("suggest").addEventListener("click", async () => {
  const metric = $("suggest-metric").value;
  if (!metric) return;
  const s = await api("GET", `baseline/suggest?metric=${encodeURIComponent(metric)}`);
  state.suggested = s.suggested;
  $("suggest-why").textContent = s.suggested
    ? `Median ${fmt(s.median)}, MAD ${fmt(s.mad)} over ${s.history_runs} runs. ${s.rationale}.`
    : `Not enough history yet: ${s.rationale}. Record more sessions first.`;
  $("suggest-yaml").textContent = s.suggested ? ruleYaml(s.suggested) : "";
  $("suggest-yaml").hidden = $("apply").hidden = !s.suggested;
});

$("apply").addEventListener("click", async () => {
  try {
    const { yaml } = await api("GET", "config");
    const r = await api("PUT", "config", { yaml: mergeRule(yaml, state.suggested) });
    $("baseline-msg").textContent = `Saved. steadygate.yml now has ${r.rules} rule(s).`;
  } catch (err) {
    $("baseline-msg").textContent = `Not saved: ${err.message}`;
  }
});

// --- gate ---------------------------------------------------------------------------

$("gate").addEventListener("click", async () => {
  const g = await api("POST", `sessions/${state.session}/gate`);
  // The engine passes a run whose required rules have no baseline yet; say so instead of
  // showing a green PASS that judged nothing.
  const required = g.rules.filter((o) => o.required);
  const shown = g.verdict === "pass" && required.length && required.every((o) => o.outcome === "no_baseline")
    ? "no_baseline" : g.verdict;
  const v = $("verdict");
  v.className = `verdict ${shown}`;
  v.textContent = shown.replace("_", " ").toUpperCase() + (g.interrupted ? " (interrupted session)" : "");
  v.hidden = false;
  $("outcomes").replaceChildren(
    ...g.rules.map((o) =>
      el("li", {},
        el("span", { className: `tag ${o.outcome}`, textContent: o.outcome.replace("_", " ").toUpperCase() }),
        `${o.rule}${o.required ? "" : " (optional)"}: ${o.detail}`),
    ),
  );
  refresh();
});

// --- snippets -----------------------------------------------------------------------

function renderSnippet() {
  const url = `${location.origin}/sessions/${state.session}/metrics`;
  const snippets = {
    curl: `curl -X POST ${url} \\\n  -H "content-type: application/json" \\\n  -d '{"metric": "frame_ms", "value": 16.4}'`,
    python: `import json, urllib.request\n\nreq = urllib.request.Request(\n    "${url}",\n    data=json.dumps({"metric": "frame_ms", "value": 16.4}).encode(),\n    headers={"content-type": "application/json"},\n)\nurllib.request.urlopen(req)`,
    js: `await fetch("${url}", {\n  method: "POST",\n  headers: { "content-type": "application/json" },\n  body: JSON.stringify({ metric: "frame_ms", value: 16.4 }),\n});`,
  };
  $("snippet").textContent = snippets[state.lang];
}

for (const tab of document.querySelectorAll(".tabs button")) {
  tab.addEventListener("click", () => {
    state.lang = tab.dataset.lang;
    for (const t of document.querySelectorAll(".tabs button")) t.setAttribute("aria-selected", String(t === tab));
    renderSnippet();
  });
}

$("copy").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("snippet").textContent);
  $("copy").textContent = "Copied";
  setTimeout(() => ($("copy").textContent = "Copy"), 1200);
});

loadCollectors();
