"use strict";

// Knob state for the live demo: turns scenario result rows into slider/toggle state and back.
// Dependency-free; in the browser these are globals, under node they are exports.

const CRASH_MESSAGE = "LLM judge timed out after 30s";
const DEFAULT_ACCURACY = 0.9;
const DEFAULT_LATENCY = 840;
const TRIALS = 10;

function row(c, metric, value, status, message) {
  return { case: c, metric: metric, value: value, status: status, message: message };
}

function knobsFromResults(suite, results, expectedCases) {
  const names = expectedCases.slice();
  for (const r of results) if (names.indexOf(r.case) < 0) names.push(r.case);
  const cases = {};
  if (suite === "trials") {
    for (const c of names) {
      cases[c] = {
        passes: results.filter((r) => r.case === c && r.value === 1).length,
      };
    }
    return { cases: cases };
  }
  for (const c of names) {
    const acc = results.find((r) => r.case === c && r.metric === "accuracy");
    const lat = results.find((r) => r.case === c && r.metric === "latency_ms");
    const accOk = acc && acc.status !== "error" && typeof acc.value === "number";
    cases[c] = {
      accuracy: accOk ? acc.value : DEFAULT_ACCURACY,
      crashed: !!acc && acc.status === "error",
      dropped: !results.some((r) => r.case === c),
      latency: lat && typeof lat.value === "number" ? lat.value : DEFAULT_LATENCY,
    };
  }
  return { cases: cases, latencyFactor: 1 };
}

function resultsFromKnobs(suite, knobs) {
  const out = [];
  if (suite === "trials") {
    for (const c of Object.keys(knobs.cases)) {
      const passes = knobs.cases[c].passes;
      for (let i = 0; i < TRIALS; i++) out.push(row(c, "passed", i < passes ? 1 : 0, "ok", ""));
    }
    return out;
  }
  const factor = knobs.latencyFactor === undefined ? 1 : knobs.latencyFactor;
  for (const c of Object.keys(knobs.cases)) {
    const k = knobs.cases[c];
    if (k.dropped) continue;
    if (k.crashed) out.push(row(c, "accuracy", null, "error", CRASH_MESSAGE));
    else out.push(row(c, "accuracy", Math.round(k.accuracy * 100) / 100, "ok", ""));
    out.push(row(c, "latency_ms", Math.round(k.latency * factor), "ok", ""));
  }
  return out;
}

function runMeans(baselines, metric) {
  return baselines.map((run) => {
    const vals = run
      .filter((r) => r.metric === metric && r.status === "ok" && typeof r.value === "number")
      .map((r) => r.value);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : NaN;
  });
}

if (typeof module !== "undefined") module.exports = { knobsFromResults, resultsFromKnobs, runMeans };
