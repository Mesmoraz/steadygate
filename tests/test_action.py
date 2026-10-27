"""The composite action's wiring: inputs it validates and flags it hands the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ACTION = yaml.safe_load((Path(__file__).parents[1] / "action.yml").read_text(encoding="utf-8"))


def step(name: str) -> dict[str, Any]:
    steps: list[dict[str, Any]] = ACTION["runs"]["steps"]
    return next(s for s in steps if s.get("name") == name)


def test_missing_history_defaults_to_inconclusive() -> None:
    assert ACTION["inputs"]["missing-history"]["default"] == "inconclusive"


def test_missing_history_is_validated() -> None:
    script = step("Locate history store")["run"]
    assert "missing-history must be inconclusive or pass" in script


def test_gate_requires_history_only_on_runs_that_do_not_record() -> None:
    gate = step("Run the gate")
    assert gate["env"]["REQUIRE_HISTORY"] == "${{ steps.store.outputs.require-history }}"
    assert '[ "$REQUIRE_HISTORY" = "true" ] && args+=(--require-history)' in gate["run"]
    script = step("Locate history store")["run"]
    condition = '[ "$record" != "true" ] && [ "$MISSING_HISTORY" = "inconclusive" ]'
    assert f"require_history=$({condition}" in script
