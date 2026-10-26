from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from installer.install import (
    MARKER,
    InstallError,
    InstallResult,
    add_to_posix_path,
    add_to_windows_path,
    default_install_dir,
    install_binary,
    run_install,
)

REG_SZ, REG_EXPAND_SZ = 1, 2


class FakeRegistry:
    def __init__(self, value: tuple[str, int] | None = None) -> None:
        self.value = value

    def get_user_path(self) -> tuple[str, int] | None:
        return self.value

    def set_user_path(self, value: str, reg_type: int) -> None:
        self.value = (value, reg_type)


def test_default_dirs(tmp_path: Path) -> None:
    assert default_install_dir("windows", {"LOCALAPPDATA": r"C:\L"}, tmp_path) == (
        Path(r"C:\L") / "Programs" / "steadygate"
    )
    assert default_install_dir("linux", {}, tmp_path) == tmp_path / ".local" / "bin"
    assert default_install_dir("macos", {}, tmp_path) == tmp_path / ".local" / "bin"


def test_install_binary_copies_and_is_executable(tmp_path: Path) -> None:
    src = tmp_path / "steadygate"
    src.write_bytes(b"bin")
    dest = install_binary(src, tmp_path / "Ana María" / "bin")
    assert dest.read_bytes() == b"bin"
    if os.name != "nt":
        assert dest.stat().st_mode & 0o111


def test_windows_path_keeps_expand_sz_and_unexpanded_entries() -> None:
    reg = FakeRegistry((r"%USERPROFILE%\bin;C:\tools", REG_EXPAND_SZ))
    assert add_to_windows_path(Path(r"C:\L\Programs\steadygate"), reg) is True
    assert reg.value == (r"%USERPROFILE%\bin;C:\tools;C:\L\Programs\steadygate", REG_EXPAND_SZ)


def test_windows_path_already_present_is_noop() -> None:
    reg = FakeRegistry((r"c:\l\programs\steadygate\;C:\tools", REG_SZ))
    assert add_to_windows_path(Path(r"C:\L\Programs\steadygate"), reg) is False
    assert reg.value == (r"c:\l\programs\steadygate\;C:\tools", REG_SZ)


def test_windows_path_empty_or_missing() -> None:
    reg = FakeRegistry(None)
    add_to_windows_path(Path(r"C:\x"), reg)
    assert reg.value == (r"C:\x", REG_EXPAND_SZ)


def test_posix_profile_added_once_and_quoted(tmp_path: Path) -> None:
    home = tmp_path / "jo smith"
    home.mkdir()
    d = home / ".local" / "bin"
    changed = add_to_posix_path(d, home, "/usr/bin", "linux")
    assert changed == [home / ".profile"]
    assert add_to_posix_path(d, home, "/usr/bin", "linux") == []
    text = (home / ".profile").read_text()
    assert text.count(MARKER) == 1
    assert 'export PATH="$HOME/.local/bin:$PATH"' in text


def test_posix_dir_outside_home_uses_quoted_absolute_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    other = tmp_path / "my tools"
    add_to_posix_path(other, home, "/usr/bin", "linux")
    assert f'export PATH="{other}:$PATH"' in (home / ".profile").read_text()


def test_posix_macos_also_updates_existing_bash_profile(tmp_path: Path) -> None:
    (tmp_path / ".bash_profile").write_text("# mine\n")
    changed = add_to_posix_path(tmp_path / ".local/bin", tmp_path, "/usr/bin", "macos")
    assert changed == [tmp_path / ".zprofile", tmp_path / ".bash_profile"]
    assert (tmp_path / ".bash_profile").read_text().startswith("# mine\n")


def test_posix_already_on_path_is_noop(tmp_path: Path) -> None:
    d = tmp_path / ".local/bin"
    assert add_to_posix_path(d, tmp_path, f"/usr/bin:{d}", "linux") == []
    assert not (tmp_path / ".profile").exists()


def test_locked_binary_raises_friendly_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "s"
    src.write_bytes(b"x")

    def boom(*a: Any, **k: Any) -> None:
        raise PermissionError

    monkeypatch.setattr(shutil, "copy2", boom)
    with pytest.raises(InstallError, match="close any terminal running steadygate"):
        install_binary(src, tmp_path / "d")


def test_run_install_windows_uses_registry(tmp_path: Path) -> None:
    src = tmp_path / "s"
    src.write_bytes(b"x")
    reg = FakeRegistry(None)
    dest_dir = tmp_path / "bin"
    result = run_install(
        src, dest_dir, True, os_name="windows", env={}, home=tmp_path, registry=reg
    )
    assert isinstance(result, InstallResult)
    assert result.binary.read_bytes() == b"x"
    assert result.path_changed is True
    assert result.profiles == []
    assert reg.value == (str(dest_dir), REG_EXPAND_SZ)


def test_run_install_posix_and_no_path(tmp_path: Path) -> None:
    src = tmp_path / "s"
    src.write_bytes(b"x")
    d = tmp_path / ".local" / "bin"
    result = run_install(
        src, d, True, os_name="linux", env={"PATH": "/usr/bin"}, home=tmp_path, registry=None
    )
    assert result.path_changed is True
    assert result.profiles == [tmp_path / ".profile"]
    skipped = run_install(
        src, d, False, os_name="linux", env={"PATH": "/usr/bin"}, home=tmp_path, registry=None
    )
    assert skipped.path_changed is False
    assert skipped.profiles == []
