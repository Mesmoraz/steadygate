"""Headless tests for the setup wizard: no Tk window is ever created here."""

from __future__ import annotations

import os
import sys
import tkinter
from pathlib import Path

import pytest

from installer import wizard


def test_self_check_reports_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = tmp_path / ("steadygate.cmd" if os.name == "nt" else "steadygate")
    fake.write_text(
        "@echo steadygate 0.0.1\n" if os.name == "nt" else "#!/bin/sh\necho steadygate 0.0.1\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("STEADYGATE_CLI", str(fake))
    assert wizard.self_check() == 0
    out = capsys.readouterr().out
    assert "version=steadygate 0.0.1" in out
    assert f"cli={fake}" in out


def test_self_check_missing_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STEADYGATE_CLI", str(tmp_path / "nope"))
    assert wizard.self_check() == 1


def test_self_check_failing_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = tmp_path / ("steadygate.cmd" if os.name == "nt" else "steadygate")
    fake.write_text("@exit /b 3\n" if os.name == "nt" else "#!/bin/sh\nexit 3\n")
    fake.chmod(0o755)
    monkeypatch.setenv("STEADYGATE_CLI", str(fake))
    assert wizard.self_check() == 1


def test_embedded_cli_frozen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    name = "steadygate.exe" if sys.platform == "win32" else "steadygate"
    assert wizard.embedded_cli() == tmp_path / "bin" / name


def test_embedded_cli_not_frozen_uses_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setenv("STEADYGATE_CLI", str(tmp_path / "sg"))
    assert wizard.embedded_cli() == tmp_path / "sg"


def test_main_self_check_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard, "self_check", lambda: 0)
    assert wizard.main(["--self-check"]) == 0


def test_main_runs_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(wizard, "_run_gui", lambda: calls.append(1))
    assert wizard.main([]) == 0
    assert calls == [1]


def test_no_display_points_to_init(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> None:
        raise tkinter.TclError("no display name")

    monkeypatch.setattr(wizard, "_run_gui", boom)
    assert wizard.main([]) == 1
    assert "steadygate init" in capsys.readouterr().err


def test_os_name_mapping() -> None:
    assert wizard.os_name_for("win32") == "windows"
    assert wizard.os_name_for("darwin") == "macos"
    assert wizard.os_name_for("linux") == "linux"
