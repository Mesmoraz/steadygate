"""Load and validate ``steadygate.yml``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from steadygate.rules import RULE_TYPES, Rule

DEFAULT_CONFIG = "steadygate.yml"


class ConfigError(ValueError):
    """The configuration is missing or invalid."""


@dataclass
class Config:
    store: Path = Path(".steadygate/history.db")
    window: int = 10
    min_history: int = 3
    rules: list[Rule] = field(default_factory=list)


def _positive_int(raw: dict[str, Any], key: str, default: int) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"'{key}' must be a positive integer, got {value!r}")
    return value


def parse(raw: Any, base_dir: Path = Path()) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("config must be a mapping")
    unknown = set(raw) - {"store", "window", "min_history", "rules"}
    if unknown:
        raise ConfigError(f"unknown top-level keys: {', '.join(sorted(unknown))}")

    store = Path(raw.get("store", ".steadygate/history.db"))
    if not store.is_absolute():
        store = base_dir / store

    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise ConfigError("'rules' must be a non-empty list")

    rules: list[Rule] = []
    for i, spec in enumerate(rules_raw, start=1):
        if not isinstance(spec, dict):
            raise ConfigError(f"rule {i}: must be a mapping")
        kind = spec.get("type")
        if kind not in RULE_TYPES:
            raise ConfigError(f"rule {i}: 'type' must be one of {', '.join(RULE_TYPES)}")
        try:
            rules.append(RULE_TYPES[kind].from_spec(spec))
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"rule {i} ({kind}): {exc}") from exc

    return Config(
        store=store,
        window=_positive_int(raw, "window", 10),
        min_history=_positive_int(raw, "min_history", 3),
        rules=rules,
    )


def load(path: Path) -> Config:
    if not path.is_file():
        raise ConfigError(f"config not found: {path} (create a {DEFAULT_CONFIG})")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    return parse(raw, base_dir=path.parent)
