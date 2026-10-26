"""Page flow and answer validation for the installer wizard (no GUI code)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from steadygate.scaffold import (
    Answers,
    Format,
    PlannedFile,
    default_answers,
    plan_files,
)

_TOLERANCE_MESSAGE = "Enter a fraction between 0 and 1, e.g. 0.05 for 5%."
_NOT_GIT = "This folder isn't a git repository. The files will still be written."


class Page(Enum):
    WELCOME = "welcome"
    LOCATION = "location"
    PROJECT = "project"
    DONE = "done"


_ORDER = (Page.WELCOME, Page.LOCATION, Page.PROJECT, Page.DONE)


def parse_tolerance(text: str) -> float:
    """Parse ``0.05`` or ``5%`` into a fraction strictly between 0 and 1."""
    raw = text.strip()
    try:
        value = float(raw[:-1]) / 100 if raw.endswith("%") else float(raw)
    except ValueError:
        raise ValueError(_TOLERANCE_MESSAGE) from None
    if not 0 < value < 1:
        raise ValueError(_TOLERANCE_MESSAGE)
    return value


def _tolerance_text(value: float) -> str:
    return f"{value:g}"


@dataclass
class WizardState:
    install_dir: Path
    page: Page = Page.WELCOME
    add_to_path: bool = True
    # Task 4 tightens this to ``InstallResult | None``.
    installed: object | None = None
    setup_project: bool = False
    repo: Path | None = None
    format: Format = "promptfoo"
    results_path: str = ""
    metric: str = ""
    tolerance_text: str = ""
    eval_command: str = ""
    overwrite: set[Path] = field(default_factory=set)
    written: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        defaults = default_answers(self.format)
        self.results_path = self.results_path or defaults.results_path
        self.metric = self.metric or defaults.metric
        self.tolerance_text = self.tolerance_text or _tolerance_text(defaults.tolerance)
        self.eval_command = self.eval_command or defaults.eval_command

    def set_format(self, fmt: Format) -> None:
        self.format = fmt
        defaults = default_answers(fmt)
        self.results_path = defaults.results_path
        self.metric = defaults.metric
        self.tolerance_text = _tolerance_text(defaults.tolerance)
        self.eval_command = defaults.eval_command

    def errors(self) -> dict[str, str]:
        found: dict[str, str] = {}
        if self.page is not Page.PROJECT or not self.setup_project:
            return found
        if self.repo is None:
            found["repo"] = "Choose a folder."
        elif not self.repo.is_dir():
            found["repo"] = "That folder doesn't exist."
        if not self.results_path.strip():
            found["results_path"] = "Enter where your eval writes its results."
        if not self.metric.strip():
            found["metric"] = "Enter the metric name to gate on."
        try:
            parse_tolerance(self.tolerance_text)
        except ValueError as exc:
            found["tolerance_text"] = str(exc)
        return found

    def warnings(self) -> list[str]:
        if (
            self.page is Page.PROJECT
            and self.setup_project
            and self.repo is not None
            and self.repo.is_dir()
            and not (self.repo / ".git").exists()
        ):
            return [_NOT_GIT]
        return []

    def answers(self) -> Answers:
        """The scaffold answers. Call only when ``errors()`` is empty."""
        return Answers(
            self.format,
            self.results_path.strip(),
            self.metric.strip(),
            parse_tolerance(self.tolerance_text),
            self.eval_command.strip(),
        )

    def planned(self) -> list[PlannedFile]:
        if self.repo is None:
            return []
        return plan_files(self.repo, self.answers())

    def can_go_next(self) -> bool:
        if self.page is Page.DONE or self.errors():
            return False
        return not (self.page is Page.LOCATION and self.installed is None)

    def next(self) -> None:
        if self.can_go_next():
            self.page = _ORDER[_ORDER.index(self.page) + 1]

    def back(self) -> None:
        index = _ORDER.index(self.page)
        if index:
            self.page = _ORDER[index - 1]
