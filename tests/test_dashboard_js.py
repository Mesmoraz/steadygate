"""The dashboard's "Apply" merges a suggested rule into steadygate.yml text. Run it in node."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

RULES_JS = Path(__file__).parents[1] / "src" / "steadygate" / "web" / "rules.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

RULE = {
    "type": "relative_delta",
    "metric": "rss_mb",
    "direction": "lower_is_better",
    "max_regression": 0.05,
}

CONFIG = """\
store: history.db
rules:
  # Memory must not grow.
  - type: relative_delta
    metric: rss_mb
    max_regression: 0.10
  # CPU is noisy; report it only.
  - type: relative_delta
    metric: cpu_pct
    max_regression: 0.25
    required: false
"""


def merge(text: str, rule: dict[str, object]) -> str:
    script = (
        f"const m = require({json.dumps(str(RULES_JS))});"
        f"process.stdout.write(m.mergeRule({json.dumps(text)}, {json.dumps(rule)}));"
    )
    assert NODE is not None
    return subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout


def test_updates_existing_rule_in_place() -> None:
    out = merge(CONFIG, RULE)
    assert "# CPU is noisy; report it only." in out
    assert "# Memory must not grow." in out
    rules = yaml.safe_load(out)["rules"]
    assert rules[0] == {**RULE}
    assert rules[1] == {
        "type": "relative_delta",
        "metric": "cpu_pct",
        "max_regression": 0.25,
        "required": False,
    }


def test_keeps_other_keys_of_the_matched_rule() -> None:
    out = merge(CONFIG, {**RULE, "metric": "cpu_pct"})
    assert yaml.safe_load(out)["rules"][1]["required"] is False
    assert yaml.safe_load(out)["rules"][1]["max_regression"] == 0.05


def test_appends_new_rule() -> None:
    out = merge(CONFIG, {**RULE, "metric": "threads"})
    rules = yaml.safe_load(out)["rules"]
    assert len(rules) == 3
    assert rules[2]["metric"] == "threads"


def test_does_not_match_metric_prefix() -> None:
    out = merge(CONFIG, {**RULE, "metric": "rss"})
    assert len(yaml.safe_load(out)["rules"]) == 3


def test_adds_rules_key_when_missing() -> None:
    rules = yaml.safe_load(merge("store: history.db\n", RULE))["rules"]
    assert rules == [RULE]
