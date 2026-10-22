from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI

from steadygate.cli import main


def test_serve_port_in_use(companion_config: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        assert main(["serve", "--port", str(port), "-c", str(companion_config)]) == 3
    assert "in use" in capsys.readouterr().err


def test_serve_missing_extra(
    companion_config: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    assert main(["serve", "-c", str(companion_config)]) == 3
    assert "steadygate[companion]" in capsys.readouterr().err


def test_serve_bad_config(tmp_path: Path) -> None:
    assert main(["serve", "-c", str(tmp_path / "missing.yml")]) == 3


def test_serve_starts_uvicorn(
    companion_config: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        seen.update(kwargs, app=app)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert main(["serve", "--port", str(port), "-c", str(companion_config)]) == 0
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == port
    assert isinstance(seen["app"], FastAPI)
    assert f"http://127.0.0.1:{port}" in capsys.readouterr().out


def test_core_import_is_light() -> None:
    code = "import sys, steadygate.cli; print('fastapi' in sys.modules, 'psutil' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False False"
