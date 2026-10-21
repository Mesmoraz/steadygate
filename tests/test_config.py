from __future__ import annotations

from pathlib import Path

import pytest

from steadygate import config
from steadygate.rules import Absolute, RelativeDelta

VALID = {
    "window": 5,
    "rules": [
        {"type": "absolute", "metric": "passed", "min": 1, "scope": "case"},
        {"type": "relative_delta", "metric": "acc", "max_regression": 0.05, "required": False},
    ],
}


def test_parse_valid_config(tmp_path: Path) -> None:
    cfg = config.parse(VALID, base_dir=tmp_path)
    assert cfg.window == 5
    assert cfg.min_history == 3
    assert cfg.store == tmp_path / ".steadygate/history.db"
    assert isinstance(cfg.rules[0], Absolute)
    assert isinstance(cfg.rules[1], RelativeDelta)
    assert cfg.rules[1].required is False


def test_absolute_store_path_kept(tmp_path: Path) -> None:
    store = tmp_path / "abs.db"
    cfg = config.parse({**VALID, "store": str(store)}, base_dir=Path("elsewhere"))
    assert cfg.store == store


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "mapping"),
        ({"rules": []}, "non-empty list"),
        ({"rules": "x"}, "non-empty list"),
        ({**VALID, "extra": 1}, "unknown top-level keys: extra"),
        ({**VALID, "window": 0}, "positive integer"),
        ({**VALID, "min_history": True}, "positive integer"),
        ({"rules": ["absolute"]}, "rule 1: must be a mapping"),
        ({"rules": [{"type": "magic", "metric": "m"}]}, "rule 1: 'type' must be one of"),
        ({"rules": [{"type": "absolute", "min": 1}]}, r"rule 1 \(absolute\): 'metric'"),
        ({"rules": [{"type": "absolute", "metric": "m"}]}, "rule 1 \\(absolute\\): needs"),
    ],
)
def test_parse_rejects_invalid(raw: object, message: str) -> None:
    with pytest.raises(config.ConfigError, match=message):
        config.parse(raw)


def test_load_missing_and_bad_yaml(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="not found"):
        config.load(tmp_path / "steadygate.yml")
    bad = tmp_path / "steadygate.yml"
    bad.write_text("rules: [unclosed", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="not valid YAML"):
        config.load(bad)
