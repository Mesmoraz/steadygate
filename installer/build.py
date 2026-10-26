"""Build the standalone CLI and the setup wizard with PyInstaller.

    python installer/build.py --out dist-installer

leaves ``<out>/steadygate[.exe]`` and ``<out>/steadygate-setup-<os>[...]`` at the root of
``--out``; PyInstaller's work and spec files go under ``<out>/work``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "installer"
ASSETS = {
    "windows": "steadygate-setup-windows.exe",
    "macos": "steadygate-setup-macos.zip",
    "linux": "steadygate-setup-linux",
}


def current_os() -> str:
    if sys.platform == "win32":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def _pyinstaller(out: Path, distpath: Path) -> list[str]:
    work = out / "work"
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(distpath),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        "--paths",
        str(ROOT),
        "--paths",
        str(ROOT / "src"),
    ]


def cli_command(os_name: str, out: Path) -> list[str]:
    return [
        *_pyinstaller(out, out),
        "--onefile",
        "--name",
        "steadygate",
        "--collect-submodules",
        "steadygate",
        str(HERE / "cli_entry.py"),
    ]


def wizard_command(os_name: str, out: Path, cli: Path) -> list[str]:
    mode = ["--onedir", "--windowed"] if os_name == "macos" else ["--onefile"]
    if os_name == "windows":
        mode.append("--windowed")
    return [
        *_pyinstaller(out, out / "setup"),
        *mode,
        "--name",
        "steadygate-setup",
        "--add-data",
        f"{cli}{os.pathsep}bin",
        str(HERE / "wizard.py"),
    ]


def package(os_name: str, out: Path) -> Path:
    """Move or zip the built wizard to its fixed asset name at the root of ``out``."""
    setup = out / "setup"
    asset = out / ASSETS[os_name]
    asset.unlink(missing_ok=True)
    if os_name == "macos":
        subprocess.run(
            ["ditto", "-c", "-k", "--keepParent", str(setup / "steadygate-setup.app"), str(asset)],
            check=True,
        )
    else:
        exe = setup / ("steadygate-setup.exe" if os_name == "windows" else "steadygate-setup")
        shutil.move(exe, asset)
    shutil.rmtree(setup)
    return asset


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("dist-installer"))
    args = parser.parse_args(argv)
    out: Path = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    os_name = current_os()
    cli = out / ("steadygate.exe" if os_name == "windows" else "steadygate")
    subprocess.run(cli_command(os_name, out), check=True)
    subprocess.run(wizard_command(os_name, out, cli), check=True)
    asset = package(os_name, out)
    print(cli)
    print(asset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
