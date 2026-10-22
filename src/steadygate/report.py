"""Render a ``GateResult`` for humans (terminal, markdown) and machines (JSON)."""

from __future__ import annotations

import json
from typing import Any

from steadygate.models import GateResult, Outcome, Verdict

_VERDICT_TEXT = {
    Verdict.PASS: "PASS",
    Verdict.REGRESSION: "REGRESSION",
    Verdict.INCONCLUSIVE: "INCONCLUSIVE",
    Verdict.CONFIG_ERROR: "CONFIG ERROR",
}
_VERDICT_EMOJI = {
    Verdict.PASS: "✅",
    Verdict.REGRESSION: "❌",
    Verdict.INCONCLUSIVE: "⚠️",
    Verdict.CONFIG_ERROR: "🛑",
}
_OUTCOME_EMOJI = {
    Outcome.PASS: "✅",
    Outcome.FAIL: "❌",
    Outcome.INCONCLUSIVE: "⚠️",
    Outcome.NO_BASELINE: "🆕",
}
_MAX_ERRORS_SHOWN = 10


def text(result: GateResult) -> str:
    lines = [f"steadygate: {_VERDICT_TEXT[result.verdict]}"]
    for o in result.outcomes:
        tag = o.outcome.value.upper()
        opt = "" if o.required else " (optional)"
        lines.append(f"  [{tag}] {o.rule}{opt}: {o.detail}")
    if result.errors:
        lines.append(f"  {len(result.errors)} result(s) errored:")
        for r in result.errors[:_MAX_ERRORS_SHOWN]:
            lines.append(f"    - {r.case} [{r.metric}] {r.message}".rstrip())
        if len(result.errors) > _MAX_ERRORS_SHOWN:
            lines.append(f"    ... and {len(result.errors) - _MAX_ERRORS_SHOWN} more")
    return "\n".join(lines)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def markdown(result: GateResult) -> str:
    v = result.verdict
    lines = [
        f"### {_VERDICT_EMOJI[v]} Steadygate: {_VERDICT_TEXT[v]}",
        "",
        "| | Rule | Required | Detail |",
        "|---|---|---|---|",
    ]
    for o in result.outcomes:
        req = "yes" if o.required else "no"
        lines.append(f"| {_OUTCOME_EMOJI[o.outcome]} | `{o.rule}` | {req} | {_cell(o.detail)} |")
    if result.errors:
        lines += ["", f"<details><summary>{len(result.errors)} result(s) errored</summary>", ""]
        for r in result.errors[:_MAX_ERRORS_SHOWN]:
            lines.append(f"- `{r.case}` [{r.metric}] {_cell(r.message)}".rstrip())
        if len(result.errors) > _MAX_ERRORS_SHOWN:
            lines.append(f"- ... and {len(result.errors) - _MAX_ERRORS_SHOWN} more")
        lines += ["", "</details>"]
    lines += ["", f"<sub>{result.total_results} results evaluated · exit code {int(v)}</sub>"]
    return "\n".join(lines) + "\n"


def as_dict(result: GateResult) -> dict[str, Any]:
    return {
        "verdict": _VERDICT_TEXT[result.verdict].lower().replace(" ", "_"),
        "exit_code": int(result.verdict),
        "total_results": result.total_results,
        "rules": [
            {
                "rule": o.rule,
                "metric": o.metric,
                "required": o.required,
                "outcome": o.outcome.value,
                "detail": o.detail,
                "observed": o.observed,
                "baseline": o.baseline,
            }
            for o in result.outcomes
        ],
        "errors": [
            {"case": r.case, "metric": r.metric, "message": r.message} for r in result.errors
        ],
    }


def as_json(result: GateResult) -> str:
    return json.dumps(as_dict(result), indent=2)
