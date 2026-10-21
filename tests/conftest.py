from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from steadygate.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "history.db")
    yield s  # type: ignore[misc]
    s.close()


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


# Base interpreter, not the venv launcher: on Windows the launcher is a separate idle
# process whose pid would not be the one burning CPU.
_PYTHON = getattr(sys, "_base_executable", sys.executable)
_BUSY = Path(__file__).parent / "helpers" / "busy.py"


def _spawn() -> subprocess.Popen[bytes]:
    return subprocess.Popen([_PYTHON, str(_BUSY), "30"])


@pytest.fixture
def busy_proc() -> Iterator[subprocess.Popen[bytes]]:
    p = _spawn()
    yield p
    p.kill()
    p.wait()


@pytest.fixture
def two_busy_procs() -> Iterator[tuple[subprocess.Popen[bytes], subprocess.Popen[bytes]]]:
    older = _spawn()
    time.sleep(0.1)
    newer = _spawn()
    yield older, newer
    for p in (older, newer):
        p.kill()
        p.wait()


COMPANION_YML = """\
store: history.db
min_history: 3
rules:
  - type: relative_delta
    metric: m
    max_regression: 0.05
"""


@pytest.fixture
def companion_config(tmp_path: Path) -> Path:
    path = tmp_path / "steadygate.yml"
    path.write_text(COMPANION_YML, encoding="utf-8")
    return path


@pytest.fixture
def client(companion_config: Path) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from steadygate.api import create_app

    with TestClient(create_app(companion_config)) as c:
        yield c
