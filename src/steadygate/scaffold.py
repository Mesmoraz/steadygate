"""Write a starter ``steadygate.yml`` and GitHub workflow from a few answers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

Format = Literal["promptfoo", "pytest-json", "junit", "jsonl"]
FORMATS: tuple[Format, ...] = get_args(Format)

ACTION_REF = "v1"
CONFIG_PATH = Path("steadygate.yml")
WORKFLOW_PATH = Path(".github/workflows/steadygate.yml")

_PASS_RATE_FORMATS = ("pytest-json", "junit")
_PLAIN_YAML = re.compile(r"[A-Za-z0-9_./-]+")


@dataclass(frozen=True)
class Answers:
    format: Format
    results_path: str
    metric: str
    tolerance: float = 0.05
    eval_command: str = ""


@dataclass(frozen=True)
class PlannedFile:
    path: Path
    content: str
    exists: bool


def default_answers(format: Format) -> Answers:
    """Sensible starting answers for each results format."""
    if format == "promptfoo":
        return Answers(
            format,
            "results.json",
            "score",
            0.05,
            "npx promptfoo eval --repeat 5 --output results.json",
        )
    if format == "pytest-json":
        return Answers(format, "report.json", "passed")
    if format == "junit":
        return Answers(format, "junit.xml", "passed")
    return Answers(format, "results.jsonl", "accuracy")


def _scalar(value: str) -> str:
    """A YAML scalar: plain when that is safe, double-quoted otherwise."""
    return value if _PLAIN_YAML.fullmatch(value) else json.dumps(value)


def _percent(fraction: float) -> str:
    return f"{round(fraction * 100, 4):g}%"


def render_config(a: Answers) -> str:
    """The starter ``steadygate.yml``, with one plain-words comment per rule."""
    metric = _scalar(a.metric)
    if a.format in _PASS_RATE_FORMATS:
        floor = round(1 - a.tolerance, 4)
        rule = (
            f"  # Each case should pass at least {_percent(floor)} of the time.\n"
            "  - type: pass_rate\n"
            f"    metric: {metric}\n"
            f"    min: {floor}\n"
            "    scope: case\n"
        )
    else:
        rule = (
            f"  # The mean {a.metric} may not drop more than {_percent(a.tolerance)}"
            " below recent runs.\n"
            "  - type: relative_delta\n"
            f"    metric: {metric}\n"
            f"    max_regression: {a.tolerance}\n"
        )
    return (
        "# Steadygate config. Change the numbers below to make the gate stricter or looser.\n"
        "store: .steadygate/history.db   # baseline history (relative to this file)\n"
        "window: 10                      # compare against the last 10 recorded runs\n"
        "min_history: 3                  # start gating once 3 runs are recorded\n"
        "\n"
        "rules:\n" + rule
    )


def render_workflow(a: Answers) -> str:
    """The GitHub workflow that runs the evals and hands the results to the action."""
    results = a.results_path.replace("\\", "/")
    if a.eval_command:
        eval_step = f"      - run: {_scalar(a.eval_command)}\n"
    else:
        eval_step = '      # TODO: run your evals here\n      - run: echo "Run your evals here"\n'
    # Failing evals exit non-zero; keep going so the gate still judges (and records) them.
    eval_step += "        continue-on-error: true\n"
    return (
        "name: steadygate\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "  pull_request:\n"
        "\n"
        "jobs:\n"
        "  gate:\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        "      contents: read\n"
        "      pull-requests: write\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        f"{eval_step}"
        f"      - uses: Mesmoraz/steadygate@{ACTION_REF}\n"
        "        with:\n"
        f"          results: {_scalar(results)}\n"
    )


def plan_files(repo: Path, a: Answers) -> list[PlannedFile]:
    """The files to write, config first and workflow second."""
    repo = repo.resolve()
    planned = []
    for rel, content in (
        (CONFIG_PATH, render_config(a)),
        (WORKFLOW_PATH, render_workflow(a)),
    ):
        path = repo / rel
        planned.append(PlannedFile(path, content, path.exists()))
    return planned


def write_files(planned: list[PlannedFile], overwrite: set[Path]) -> list[Path]:
    """Write each planned file, skipping existing ones not listed in ``overwrite``."""
    written = []
    for item in planned:
        if item.exists and item.path not in overwrite:
            continue
        item.path.parent.mkdir(parents=True, exist_ok=True)
        with item.path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(item.content)
        written.append(item.path)
    return written
