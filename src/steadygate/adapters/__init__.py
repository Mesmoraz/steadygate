"""Adapters turn another tool's output into a list of ``Result``."""

from __future__ import annotations

from pathlib import Path

from steadygate.adapters import jsonl, junit
from steadygate.models import Result

ADAPTERS = {
    "jsonl": jsonl.load,
    "junit": junit.load,
}


class AdapterError(ValueError):
    """The results file could not be read or parsed."""


def detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".xml":
        return "junit"
    if suffix in {".jsonl", ".ndjson"}:
        return "jsonl"
    raise AdapterError(
        f"can't tell the format of {path.name}; pass --format ({', '.join(ADAPTERS)})"
    )


def load_results(path: Path, fmt: str | None = None) -> list[Result]:
    fmt = fmt or detect_format(path)
    if fmt not in ADAPTERS:
        raise AdapterError(f"unknown format {fmt!r}; expected one of {', '.join(ADAPTERS)}")
    if not path.is_file():
        raise AdapterError(f"results file not found: {path}")
    try:
        return ADAPTERS[fmt](path)
    except AdapterError:
        raise
    except Exception as exc:  # parsers raise many types; surface them uniformly
        raise AdapterError(f"could not parse {path} as {fmt}: {exc}") from exc
