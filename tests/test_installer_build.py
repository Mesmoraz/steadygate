"""The PyInstaller command lines built by installer/build.py (nothing is built here)."""

from __future__ import annotations

from pathlib import Path

import pytest

from installer import build

OUT = Path("/o")


def test_cli_is_onefile_everywhere() -> None:
    for os_name in ("windows", "macos", "linux"):
        cmd = build.cli_command(os_name, OUT)
        assert "--onefile" in cmd
        assert cmd[cmd.index("--name") + 1] == "steadygate"
        assert cmd[cmd.index("--distpath") + 1] == str(OUT)
        assert cmd[cmd.index("--workpath") + 1] == str(OUT / "work")
        assert cmd[cmd.index("--specpath") + 1] == str(OUT / "work")
        assert cmd[-1].endswith("cli_entry.py")


@pytest.mark.parametrize(
    ("os_name", "mode", "windowed"),
    [("windows", "--onefile", True), ("linux", "--onefile", False), ("macos", "--onedir", True)],
)
def test_wizard_command(os_name: str, mode: str, windowed: bool) -> None:
    cli = OUT / "steadygate.exe"
    cmd = build.wizard_command(os_name, OUT, cli)
    assert mode in cmd
    assert ("--windowed" in cmd) is windowed
    assert cmd[cmd.index("--add-data") + 1].endswith("bin")
    assert cmd[cmd.index("--add-data") + 1].startswith(str(cli))
    assert cmd[cmd.index("--distpath") + 1] == str(OUT / "setup")
    assert cmd[-1].endswith("wizard.py")


def test_asset_names() -> None:
    assert build.ASSETS == {
        "windows": "steadygate-setup-windows.exe",
        "macos": "steadygate-setup-macos.zip",
        "linux": "steadygate-setup-linux",
    }


def test_release_workflow_triggers() -> None:
    import yaml

    path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
    wf = yaml.safe_load(path.read_text(encoding="utf-8"))
    on = wf[True]  # PyYAML reads the bare key "on" as a boolean
    assert on["push"]["tags"] == ["v[0-9]+.[0-9]+.[0-9]+"]
    assert sorted(on["pull_request"]["paths"]) == sorted(
        ["installer/**", ".github/workflows/release.yml", "pyproject.toml", "src/**"]
    )
    assert "workflow_dispatch" in on
    assert wf["jobs"]["release"]["if"] == "startsWith(github.ref, 'refs/tags/v')"
