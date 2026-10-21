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
    import psutil

    p = subprocess.Popen([_PYTHON, str(_BUSY), "30"])
    # Wait until the child is burning CPU. On macOS the framework python3.x binary re-execs
    # itself as "Python" under the same pid, so its name isn't stable until it is running.
    proc = psutil.Process(p.pid)
    deadline = time.monotonic() + 10
    while proc.cpu_times().user < 0.05 and time.monotonic() < deadline:
        time.sleep(0.02)
    return p


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

    # The companion only answers to loopback Host headers, not TestClient's "testserver".
    with TestClient(create_app(companion_config), base_url="http://127.0.0.1") as c:
        yield c
