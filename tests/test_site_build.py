from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def build() -> ModuleType:
    return _load("sg_build", REPO / "site" / "build.py")


@pytest.fixture(scope="module")
def demo() -> ModuleType:
    return _load("sg_demo", REPO / "site" / "demo.py")


def test_verdicts_match_the_contract(build: ModuleType) -> None:
    data = build.build_scenarios(REPO)
    got = {
        (s, x["id"]): x["report"]["exit_code"]
        for s, suite in data["suites"].items()
        for x in suite["scenarios"]
    }
    assert got == {
        ("support", "noise"): 0,
        ("support", "regression"): 1,
        ("support", "broken"): 2,
        ("support", "missing"): 2,
        ("trials", "noise"): 0,
        ("trials", "broken"): 1,
    }


def test_demo_engine_matches_the_cli(build: ModuleType, demo: ModuleType) -> None:
    for suite in build.build_scenarios(REPO)["suites"].values():
        for sc in suite["scenarios"]:
            out = demo.run(sc["results"], suite["config"], suite["baselines"])
            assert out["report"] == sc["report"], sc["id"]
            assert out["markdown"] == sc["markdown"], sc["id"]


def test_wrong_expectation_fails_the_build(
    build: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(build.SCENARIOS["support"]["scenarios"][0], "expected", 1)
    with pytest.raises(build.BuildError, match="support/noise"):
        build.build_scenarios(REPO)


def test_assemble_writes_site(build: ModuleType, tmp_path: Path) -> None:
    wheel = tmp_path / "steadygate-0.0.1-py3-none-any.whl"
    wheel.write_bytes(b"x")
    build.assemble(REPO, tmp_path / "out", wheel)
    latest = json.loads((tmp_path / "out/wheels/latest.json").read_text())
    assert latest == {"wheel": wheel.name}
    data = json.loads((tmp_path / "out/data/scenarios.json").read_text())
    assert "support" in data["suites"]
    assert (tmp_path / "out/demo.py").exists()


def test_site_uses_relative_urls():
    for name in ("index.html", "app.js", "style.css"):
        text = (REPO / "site" / name).read_text(encoding="utf-8")
        assert not re.search(r"""(?:src|href|fetch\()\s*=?\s*["']/(?!/)""", text), name


def test_site_has_install_and_audience(build: ModuleType) -> None:
    html = (REPO / "site" / "index.html").read_text(encoding="utf-8")
    for name in (
        "steadygate-setup-windows.exe",
        "steadygate-setup-macos.zip",
        "steadygate-setup-linux",
    ):
        assert f"releases/latest/download/{name}" in html
    assert 'id="who"' in html and 'id="install"' in html and 'id="trust"' in html
    assert (
        "If your tests give the same answer every time, a plain pass/fail is all you need." in html
    )
    assert "chmod +x steadygate-setup-linux" in html
    assert "Your eval passed." in html
    assert "Runs entirely in your browser. Nothing is sent anywhere." in html
    assert "install.js" in build.SITE_FILES
    assert html.index("install.js") < html.index("app.js")
