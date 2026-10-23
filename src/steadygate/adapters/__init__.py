"""Adapters turn another tool's output into a list of ``Result``."""

from __future__ import annotations

import json
from pathlib import Path

from steadygate.adapters import jsonl, junit, promptfoo, pytest_json
from steadygate.models import Result

ADAPTERS = {
    "jsonl": jsonl.load,
    "junit": junit.load,
    "promptfoo": promptfoo.load,
    "pytest-json": pytest_json.load,
}


class AdapterError(ValueError):
    """The results file could not be read or parsed."""


def _sniff_json(path: Path) -> str:
    """Tell pytest-json-report from promptfoo output by their top-level keys."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AdapterError(f"could not read {path} as JSON: {exc}") from exc
    if isinstance(data, dict):
        if isinstance(data.get("tests"), list):
            return "pytest-json"
        if "results" in data:
            return "promptfoo"
    raise AdapterError(
        f"can't tell which tool wrote {path.name}; pass --format ({', '.join(ADAPTERS)})"
    )


def detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".xml":
        return "junit"
    if suffix in {".jsonl", ".ndjson"}:
        return "jsonl"
    if suffix == ".json":
        return _sniff_json(path)
    raise AdapterError(
        f"can't tell the format of {path.name}; pass --format ({', '.join(ADAPTERS)})"
    )


def load_results(path: Path, fmt: str | None = None) -> list[Result]:
    if fmt is not None and fmt not in ADAPTERS:
        raise AdapterError(f"unknown format {fmt!r}; expected one of {', '.join(ADAPTERS)}")
    if not path.is_file():
        raise AdapterError(f"results file not found: {path}")
    fmt = fmt or detect_format(path)
    try:
        return ADAPTERS[fmt](path)
    except AdapterError:
        raise
    except Exception as exc:  # parsers raise many types; surface them uniformly
        raise AdapterError(f"could not parse {path} as {fmt}: {exc}") from exc
