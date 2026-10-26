from __future__ import annotations

from pathlib import Path

import pytest

from installer.state import Page, WizardState, parse_tolerance


def state(**kw: object) -> WizardState:
    return WizardState(install_dir=Path("/x"), **kw)  # type: ignore[arg-type]


def test_page_order_and_install_gate() -> None:
    s = state()
    s.next()
    assert s.page is Page.LOCATION
    assert not s.can_go_next()
    s.installed = object()
    s.next()
    assert s.page is Page.PROJECT
    s.back()
    assert s.page is Page.LOCATION


def test_project_page_skipped_when_unchecked_goes_to_done() -> None:
    s = state(page=Page.PROJECT, setup_project=False)
    assert s.can_go_next()
    s.next()
    assert s.page is Page.DONE


def test_project_errors(tmp_path: Path) -> None:
    s = state(page=Page.PROJECT, setup_project=True)
    assert s.errors()["repo"] == "Choose a folder."
    s.repo = tmp_path
    s.metric = ""
    assert s.errors() == {"metric": "Enter the metric name to gate on."}


@pytest.mark.parametrize("text,value", [("0.05", 0.05), ("5%", 0.05), ("12.5%", 0.125)])
def test_tolerance_accepts(text: str, value: float) -> None:
    assert parse_tolerance(text) == pytest.approx(value)


@pytest.mark.parametrize("text", ["5", "0", "1", "-0.1", "abc", ""])
def test_tolerance_rejects(text: str) -> None:
    with pytest.raises(ValueError, match=r"0.05"):
        parse_tolerance(text)


def test_set_format_refills_defaults() -> None:
    s = state()
    s.metric = "custom"
    s.set_format("junit")
    assert (s.metric, s.results_path) == ("passed", "junit.xml")


def test_non_git_folder_is_a_warning_not_an_error(tmp_path: Path) -> None:
    s = state(page=Page.PROJECT, setup_project=True, repo=tmp_path)
    assert s.errors() == {}
    assert s.warnings() == ["This folder isn't a git repository. The files will still be written."]


def test_git_folder_has_no_warning(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    s = state(page=Page.PROJECT, setup_project=True, repo=tmp_path)
    assert s.warnings() == []


def test_bad_tolerance_and_results_path_are_errors_then_answers(tmp_path: Path) -> None:
    s = state(page=Page.PROJECT, setup_project=True, repo=tmp_path)
    s.tolerance_text = "5"
    assert list(s.errors()) == ["tolerance_text"]
    s.tolerance_text = "10%"
    s.results_path = ""
    assert list(s.errors()) == ["results_path"]
    s.results_path = "out/r.json"
    assert s.errors() == {}
    assert s.answers().tolerance == pytest.approx(0.1)
    assert len(s.planned()) == 2


def test_next_stops_at_done_and_back_stops_at_welcome() -> None:
    s = state(page=Page.DONE)
    s.next()
    assert s.page is Page.DONE
    s = state()
    s.back()
    assert s.page is Page.WELCOME
