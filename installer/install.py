"""Copy the CLI into place and put it on PATH (no GUI code)."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

MARKER = "# added by steadygate-setup"
_REG_EXPAND_SZ = 2
_LOCKED = "Couldn't replace steadygate. Please close any terminal running steadygate and try again."


class InstallError(Exception):
    """A problem the wizard can show to the user as-is."""


class Registry(Protocol):
    def get_user_path(self) -> tuple[str, int] | None:
        """Return ``(value, reg_type)`` of the user ``Path``, or ``None`` if unset."""
        ...

    def set_user_path(self, value: str, reg_type: int) -> None: ...


class WinRegistry:
    """The real per-user registry. Only usable on Windows."""

    def get_user_path(self) -> tuple[str, int] | None:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                try:
                    value, reg_type = winreg.QueryValueEx(key, "Path")
                except FileNotFoundError:
                    return None
            return str(value), int(reg_type)
        raise InstallError("The Windows registry is only available on Windows.")

    def set_user_path(self, value: str, reg_type: int) -> None:
        if sys.platform == "win32":
            import ctypes
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, "Path", 0, reg_type, value)
            result = ctypes.c_size_t()
            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF,  # HWND_BROADCAST
                0x001A,  # WM_SETTINGCHANGE
                0,
                "Environment",
                0x0002,  # SMTO_ABORTIFHUNG
                5000,
                ctypes.byref(result),
            )
            return
        raise InstallError("The Windows registry is only available on Windows.")


@dataclass
class InstallResult:
    binary: Path
    path_changed: bool
    profiles: list[Path] = field(default_factory=list)


def default_install_dir(os_name: str, env: Mapping[str, str], home: Path) -> Path:
    if os_name == "windows":
        base = Path(env["LOCALAPPDATA"]) if env.get("LOCALAPPDATA") else home / "AppData" / "Local"
        return base / "Programs" / "steadygate"
    return home / ".local" / "bin"


def install_binary(src: Path, dest_dir: Path) -> Path:
    """Copy ``src`` beside the target, then swap it in, so a reinstall never edits in place."""
    dest = dest_dir / ("steadygate.exe" if sys.platform == "win32" else "steadygate")
    tmp: Path | None = None
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".steadygate-", suffix=".tmp", dir=dest_dir)
        os.close(fd)
        tmp = Path(name)
        shutil.copy2(src, tmp)
        tmp.chmod(0o755)
        os.replace(tmp, dest)
        tmp = None
    except PermissionError:
        if dest.exists():
            raise InstallError(_LOCKED) from None
        raise InstallError(
            f"You don't have permission to write to {dest_dir}. "
            "Choose a folder in your home directory."
        ) from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return dest


def _norm_win(entry: str) -> str:
    return entry.strip().rstrip("\\/").lower()


def add_to_windows_path(directory: Path, reg: Registry) -> bool:
    """Append ``directory`` to the user PATH, keeping its type and ``%VAR%`` entries as-is."""
    current = reg.get_user_path()
    value, reg_type = current if current is not None else ("", _REG_EXPAND_SZ)
    wanted = str(directory)
    if any(_norm_win(entry) == _norm_win(wanted) for entry in value.split(";") if entry.strip()):
        return False
    new = f"{value.rstrip(';')};{wanted}" if value.strip() else wanted
    reg.set_user_path(new, reg_type)
    return True


def _profile_files(home: Path, os_name: str) -> list[Path]:
    if os_name == "macos":
        files = [home / ".zprofile"]
        if (home / ".bash_profile").exists():
            files.append(home / ".bash_profile")
        return files
    # Login shells read ~/.profile; the non-login shells most terminals open read ~/.bashrc.
    files = [home / ".profile", home / ".bashrc"]
    if (home / ".zshrc").exists():
        files.append(home / ".zshrc")
    return files


def _export_line(directory: Path, home: Path) -> str:
    try:
        rel = directory.relative_to(home).as_posix()
        shown = "$HOME" if rel == "." else f"$HOME/{rel}"
    except ValueError:
        shown = str(directory)
    return f'export PATH="{shown}:$PATH"'


def add_to_posix_path(directory: Path, home: Path, env_path: str, os_name: str) -> list[Path]:
    """Append an export line to the shell startup files. Returns the files that changed."""
    wanted = directory.resolve()
    for entry in env_path.split(":"):
        if entry and Path(entry).resolve() == wanted:
            return []
    line = _export_line(directory, home)
    changed: list[Path] = []
    for profile in _profile_files(home, os_name):
        text = profile.read_text(encoding="utf-8") if profile.exists() else ""
        if MARKER in text and line in text:
            continue
        prefix = "" if not text or text.endswith("\n") else "\n"
        with profile.open("a", encoding="utf-8") as fh:
            fh.write(f"{prefix}\n{MARKER}\n{line}\n")
        changed.append(profile)
    return changed


def run_install(
    src: Path,
    dest_dir: Path,
    add_to_path: bool,
    *,
    os_name: str,
    env: Mapping[str, str],
    home: Path,
    registry: Registry | None,
) -> InstallResult:
    binary = install_binary(src, dest_dir)
    if not add_to_path:
        return InstallResult(binary, False, [])
    if os_name == "windows":
        if registry is None:
            raise InstallError("No registry available to update PATH.")
        return InstallResult(binary, add_to_windows_path(dest_dir, registry), [])
    profiles = add_to_posix_path(dest_dir, home, env.get("PATH", ""), os_name)
    return InstallResult(binary, bool(profiles), profiles)
