"""The demo site's pure JS modules (knob state, PR-comment markdown). Run them in node."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from steadygate.adapters import load_results

REPO = Path(__file__).resolve().parents[1]
KNOBS_JS = REPO / "site" / "knobs.js"
MARKDOWN_JS = REPO / "site" / "markdown.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

SUPPORT_CASES = ["refund-policy", "shipping-faq", "escalation", "order-status", "returns-window"]
TRIAL_CASES = ["book-flight", "cancel-order", "track-parcel"]


def rows(suite: str, name: str) -> list[dict[str, Any]]:
    path = REPO / "examples" / suite / name
    return [{**dataclasses.asdict(r), "status": str(r.status)} for r in load_results(path)]


def node(module: Path, expr: str) -> Any:
    script = f"const m = require({json.dumps(str(module))}); console.log(JSON.stringify({expr}));"
    assert NODE is not None
    out = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    return json.loads(out)


def knobs(suite: str, results: list[dict[str, Any]], expected: list[str]) -> Any:
    args = f"{json.dumps(suite)}, {json.dumps(results)}, {json.dumps(expected)}"
    return node(KNOBS_JS, f"m.knobsFromResults({args})")


def results_from(suite: str, k: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = node(
        KNOBS_JS, f"m.resultsFromKnobs({json.dumps(suite)}, {json.dumps(k)})"
    )
    return out


def sorted_rows(rs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rs, key=lambda r: (r["case"], r["metric"], -(r["value"] or 0)))


def render(md: str) -> str:
    out: str = node(MARKDOWN_JS, f"m.renderMarkdown({json.dumps(md)})")
    return out


def test_knobs_round_trip_support() -> None:
    noise = rows("basic", "candidate-noise.jsonl")
    k = knobs("support", noise, SUPPORT_CASES)
    assert k["latencyFactor"] == 1
    assert sorted_rows(results_from("support", k)) == sorted_rows(noise)


def test_knobs_round_trip_trials() -> None:
    noise = rows("trials", "candidate-noise.jsonl")
    k = knobs("trials", noise, TRIAL_CASES)
    assert all(0 <= c["passes"] <= 10 for c in k["cases"].values())
    out = results_from("trials", k)
    assert len(out) == 10 * len(TRIAL_CASES)
    assert sorted_rows(out) == sorted_rows(noise)


def test_rounding() -> None:
    noise = rows("basic", "candidate-noise.jsonl")
    k = knobs("support", noise, SUPPORT_CASES)
    k["cases"]["refund-policy"]["accuracy"] = 0.1 + 0.2
    k["latencyFactor"] = 1.5
    out = results_from("support", k)
    acc = [r for r in out if r["case"] == "refund-policy" and r["metric"] == "accuracy"]
    lat = [r for r in out if r["case"] == "refund-policy" and r["metric"] == "latency_ms"]
    assert acc[0]["value"] == 0.3
    assert lat[0]["value"] == round(860 * 1.5)


def test_crash_and_drop() -> None:
    k = knobs("support", rows("basic", "candidate-noise.jsonl"), SUPPORT_CASES)
    k["cases"]["escalation"]["crashed"] = True
    k["cases"]["order-status"]["dropped"] = True
    out = results_from("support", k)
    crash = [r for r in out if r["case"] == "escalation" and r["metric"] == "accuracy"]
    assert crash == [
        {
            "case": "escalation",
            "metric": "accuracy",
            "value": None,
            "status": "error",
            "message": "LLM judge timed out after 30s",
        }
    ]
    assert not [r for r in out if r["case"] == "order-status"]


def test_missing_case_loads_as_dropped() -> None:
    missing_rows = rows("basic", "candidate-missing.jsonl")
    k = knobs("support", missing_rows, SUPPORT_CASES)
    reported = {r["case"] for r in missing_rows}
    missing = [c for c in SUPPORT_CASES if c not in reported]
    assert missing
    for c in SUPPORT_CASES:
        assert k["cases"][c]["dropped"] is (c in missing)
    for c in missing:
        assert k["cases"][c]["accuracy"] == 0.9
        assert k["cases"][c]["latency"] == 840
    assert sorted_rows(results_from("support", k)) == sorted_rows(missing_rows)


def test_run_means() -> None:
    baselines = [rows("basic", f"baseline-{n}.jsonl") for n in (1, 2, 3)]
    got = node(KNOBS_JS, f"m.runMeans({json.dumps(baselines)}, 'accuracy')")
    assert len(got) == 3
    for run, mean in zip(baselines, got, strict=True):
        vals = [r["value"] for r in run if r["metric"] == "accuracy" and r["status"] == "ok"]
        assert abs(mean - sum(vals) / len(vals)) < 1e-9


@pytest.fixture(scope="module")
def broken_markdown() -> str:
    spec = importlib.util.spec_from_file_location("sg_build", REPO / "site" / "build.py")
    assert spec is not None
    assert spec.loader is not None
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    data = build.build_scenarios(REPO)
    scenarios = data["suites"]["support"]["scenarios"]
    return str(next(s for s in scenarios if s["id"] == "broken")["markdown"])


def test_markdown_renders_report(broken_markdown: str) -> None:
    html = render(broken_markdown)
    for tag in ("<h3>", "<table>", "<details>", "<code>", "<sub>", "<li>"):
        assert tag in html


def test_markdown_escaping() -> None:
    html = render(
        "### ✅ x\n\n| a | b |\n|---|---|\n| `<script>alert(1)</script>` | x \\| y |\n\n"
        "- `<img src=x onerror=alert(1)>`\n- <b onclick='x'>bad</b> & \"q\"\n"
    )
    assert "<script>" not in html
    assert "<img" not in html
    assert "<b " not in html
    assert "&lt;script&gt;" in html
    assert "x | y" in html
    assert "&amp;" in html
    assert "&quot;" in html
    assert "&#39;" in html


def test_markdown_unescapes_pipes_in_list_items() -> None:
    html = render(r"- `x` [accuracy] a \| b" + "\n")
    assert "a | b" in html
    assert r"\|" not in html


INSTALL_JS = REPO / "site" / "install.js"
RELEASE_URL = "https://github.com/Mesmoraz/steadygate/releases/latest/download/"


@pytest.mark.parametrize(
    "platform,ua,expected",
    [
        ("Windows", "", "windows"),
        ("", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "windows"),
        ("macOS", "", "macos"),
        ("", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5)", "macos"),
        ("Linux", "", "linux"),
        ("", "Mozilla/5.0 (X11; Linux x86_64)", "linux"),
        ("", "Mozilla/5.0 (Linux; Android 14)", None),
        ("Linux", "Mozilla/5.0 (Linux; Android 14)", None),
        ("", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", None),
        ("", "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)", None),
        ("", "", None),
    ],
)
def test_detect_os(platform: str, ua: str, expected: str | None) -> None:
    arg = json.dumps({"platform": platform, "userAgent": ua})
    assert node(INSTALL_JS, f"m.detectOS({arg})") == expected


def test_download_url() -> None:
    assert node(INSTALL_JS, 'm.downloadUrl("macos")') == RELEASE_URL + "steadygate-setup-macos.zip"


def test_release_api() -> None:
    assert node(INSTALL_JS, "m.RELEASE_API") == (
        "https://api.github.com/repos/Mesmoraz/steadygate/releases/latest"
    )


def _init_install(os_args: dict[str, str], fetch_body: str) -> Any:
    """Run initInstall against a tiny fake document and a fake fetch; return the final state."""
    script = f"""
const m = require({json.dumps(str(INSTALL_JS))});
const btn = {{href: "#install", textContent: "Install now", innerHTML: "", hidden: false}};
const doc = {{
  getElementById: (id) => (id === "install-now" ? btn : null),
  defaultView: {{navigator: {json.dumps(os_args)}}},
}};
const fetchFn = {fetch_body};
(async () => {{
  await m.initInstall(doc, fetchFn);
  console.log(JSON.stringify(btn));
}})();
"""
    assert NODE is not None
    out = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    return json.loads(out)


WIN = {"platform": "Win32", "userAgent": "Mozilla/5.0 (Windows NT 10.0)"}


def test_init_install_sets_button_for_known_os() -> None:
    btn = _init_install(WIN, "async () => ({ok: true, status: 200})")
    assert btn["href"] == RELEASE_URL + "steadygate-setup-windows.exe"
    assert btn["textContent"] == "Install for Windows"


def test_init_install_unknown_os_keeps_anchor() -> None:
    btn = _init_install(
        {"platform": "", "userAgent": "Android"}, "async () => ({ok: true, status: 200})"
    )
    assert btn["href"] == "#install"
    assert btn["textContent"] == "Install now"


def test_init_install_404_swaps_in_coming_soon() -> None:
    btn = _init_install(WIN, "async () => ({ok: false, status: 404})")
    assert "Coming soon" in btn["innerHTML"]
    assert "pip install git+https://github.com/Mesmoraz/steadygate" in btn["innerHTML"]


@pytest.mark.parametrize(
    "fetch_body",
    [
        "async () => ({ok: false, status: 403})",
        "async () => ({ok: false, status: 500})",
        "async () => { throw new Error('offline'); }",
        "() => { throw new Error('sync'); }",
    ],
)
def test_init_install_other_failures_change_nothing(fetch_body: str) -> None:
    btn = _init_install(WIN, fetch_body)
    assert btn["href"] == RELEASE_URL + "steadygate-setup-windows.exe"
    assert btn["textContent"] == "Install for Windows"
    assert btn["innerHTML"] == ""
