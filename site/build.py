"""Build the static live-demo site.

Runs the real Steadygate CLI over the example scenarios, checks every exit code against
the contract, and assembles the result into an output directory.

    python site/build.py --out _site [--wheel dist/steadygate-*.whl]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from steadygate.adapters import load_results

SITE_FILES = ["demo.py", "knobs.js", "markdown.js"]

SCENARIOS: dict[str, dict[str, Any]] = {
    "support": {
        "dir": "basic",
        "label": "Support agent",
        "scenarios": [
            {
                "id": "noise",
                "label": "Normal noise",
                "caption": "Accuracy is 2.2% below the baseline median, inside the 5% tolerance.",
                "file": "candidate-noise.jsonl",
                "expected": 0,
            },
            {
                "id": "regression",
                "label": "Real regression",
                "caption": "Accuracy fell 17.8%. That's a real drop, not noise.",
                "file": "candidate-regression.jsonl",
                "expected": 1,
            },
            {
                "id": "broken",
                "label": "Judge timed out",
                "caption": (
                    "The score looks better, but one case's judge timed out, so it can't pass."
                ),
                "file": "candidate-broken.jsonl",
                "expected": 2,
            },
            {
                "id": "missing",
                "label": "Case never reported",
                "caption": (
                    "One case crashed before writing any result. "
                    "The average looks fine; the gate doesn't."
                ),
                "file": "candidate-missing.jsonl",
                "expected": 2,
            },
        ],
    },
    "trials": {
        "dir": "trials",
        "label": "Repeated trials",
        "scenarios": [
            {
                "id": "noise",
                "label": "7/10 is noise",
                "caption": (
                    "Against an 80% target, 7 passes out of 10 is within the confidence interval."
                ),
                "file": "candidate-noise.jsonl",
                "expected": 0,
            },
            {
                "id": "broken",
                "label": "3/10 is real",
                "caption": "3 out of 10 is confidently below 80%, so the build fails.",
                "file": "candidate-broken.jsonl",
                "expected": 1,
            },
        ],
    },
}


class BuildError(Exception):
    """The site cannot be built, or a scenario no longer behaves as documented."""


def _rows(path: Path) -> list[dict[str, Any]]:
    return [{**dataclasses.asdict(r), "status": str(r.status)} for r in load_results(path)]


def _cli(cli: Sequence[str], args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*cli, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False
    )


def _max_regression(config_yaml: str) -> float | None:
    for rule in (yaml.safe_load(config_yaml) or {}).get("rules", []):
        if rule.get("type") == "relative_delta" and rule.get("metric") == "accuracy":
            return float(rule["max_regression"])
    return None


def _build_suite(
    repo: Path, key: str, spec: dict[str, Any], cli: Sequence[str], failures: list[str]
) -> dict[str, Any]:
    src = repo / "examples" / spec["dir"]
    config_yaml = (src / "steadygate.yml").read_text(encoding="utf-8")
    baseline_files = sorted(src.glob("baseline-*.jsonl"))
    scenarios: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        shutil.copytree(src, work, dirs_exist_ok=True)
        for n, bf in enumerate(baseline_files, start=1):
            rec = _cli(cli, ["record", bf.name, "--run-id", f"base-{n}"], work)
            if rec.returncode != 0:
                raise BuildError(f"recording {key}/{bf.name} failed: {rec.stderr.strip()}")
        for sc in spec["scenarios"]:
            md_path = work / f"{sc['id']}.md"
            proc = _cli(cli, ["run", sc["file"], "--json", "--markdown", str(md_path)], work)
            if proc.returncode != sc["expected"]:
                failures.append(
                    f"{key}/{sc['id']}: expected exit {sc['expected']}, got {proc.returncode}"
                )
                continue
            scenarios.append(
                {
                    "id": sc["id"],
                    "label": sc["label"],
                    "caption": sc["caption"],
                    "expected": sc["expected"],
                    "results": _rows(src / sc["file"]),
                    "report": json.loads(proc.stdout),
                    "markdown": md_path.read_text(encoding="utf-8"),
                }
            )
    max_regression = _max_regression(config_yaml) if baseline_files else None
    return {
        "label": spec["label"],
        "config": config_yaml,
        "baselines": [_rows(bf) for bf in baseline_files],
        "chart": (
            {"metric": "accuracy", "max_regression": max_regression}
            if max_regression is not None
            else None
        ),
        "scenarios": scenarios,
    }


def build_scenarios(
    repo: Path, cli: Sequence[str] = (sys.executable, "-m", "steadygate")
) -> dict[str, Any]:
    """Run every scenario through the real CLI and collect results, reports and markdown."""
    failures: list[str] = []
    suites = {key: _build_suite(repo, key, spec, cli, failures) for key, spec in SCENARIOS.items()}
    if failures:
        raise BuildError("; ".join(failures))
    return {"suites": suites}


def assemble(repo: Path, out: Path, wheel: Path | None) -> None:
    """Write the static site into ``out``."""
    data = build_scenarios(repo)
    out.mkdir(parents=True, exist_ok=True)
    for name in SITE_FILES:
        src = repo / "site" / name
        if not src.is_file():
            raise BuildError(f"site file missing: {src}")
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    (out / "data").mkdir(exist_ok=True)
    (out / "data" / "scenarios.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    if wheel is not None:
        (out / "wheels").mkdir(exist_ok=True)
        shutil.copyfile(wheel, out / "wheels" / wheel.name)
        (out / "wheels" / "latest.json").write_text(
            json.dumps({"wheel": wheel.name}), encoding="utf-8"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Steadygate live demo site.")
    parser.add_argument("--out", type=Path, default=Path("_site"))
    parser.add_argument("--wheel", type=Path, default=None)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parent.parent
    try:
        assemble(repo, args.out, args.wheel)
    except BuildError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
