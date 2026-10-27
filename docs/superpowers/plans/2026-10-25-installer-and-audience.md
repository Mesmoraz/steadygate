# Installer Wizard and "Who It's For" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An "Install now" button that downloads a compiled, windowed setup wizard for
Windows, Mac or Linux. The wizard installs the `steadygate` CLI and writes a starter config
and workflow. The site also makes it obvious who Steadygate is for and why.

**Architecture:** The project-setup logic lives in `src/steadygate/scaffold.py`, which has
pure functions plus one writer. Both a new `steadygate init` command and the wizard use it.
The wizard lives in `installer/` (outside the wheel) and has three parts:
- `state.py`: page and answer logic, with no Tk
- `install.py`: copying the binary and changing PATH, with no Tk
- `wizard.py`: thin Tk widgets on top of the other two

`installer/build.py` drives PyInstaller twice: first the CLI, then the wizard with the CLI
embedded. A tag-triggered release workflow builds on three operating systems,
smoke-tests the builds, and publishes files with fixed names. The site gets a small
`install.js`, which picks the OS and uses pure functions that can be tested in node, plus
new copy sections.

**Tech Stack:** Python 3.11+, Tkinter (stdlib), PyInstaller 6.x, `winreg` and `ctypes`
(Windows only), GitHub Actions (`actions/upload-artifact@v4`, `actions/download-artifact@v4`,
`gh release`), vanilla JS, pytest, and node for the JS tests.

**Spec:** `docs/superpowers/specs/2026-10-25-installer-and-audience-design.md`

**Deviations from the spec, decided while planning:**
- The spec says `steadygate.config.load_config`. The real function is
  `steadygate.config.load(path)`, and the plan uses it.
- The two PyInstaller `.spec` files are replaced by `installer/build.py`, which calls the
  PyInstaller CLI. It's one script for local and CI builds, with no generated spec files to
  keep in sync.
- `installer/cli_entry.py` is added as the PyInstaller entry point for the CLI, and
  `installer/smoke.sh` holds the shared smoke test.

## Global Constraints

- Python 3.11 floor. Follow the repo's ruff (line length 100) and `mypy --strict`. Add
  `"installer"` to `[tool.mypy] files`.
- No new runtime dependencies in `pyproject.toml`. PyInstaller is a build-only dependency,
  installed in the workflow and listed under a new `installer` extra.
- `--cov=steadygate` stays at 90% or above. `installer/` is tested but outside that figure.
- Fixed asset names: `steadygate-setup-windows.exe`, `steadygate-setup-macos.zip` and
  `steadygate-setup-linux`.
- The download URL pattern is exactly
  `https://github.com/Mesmoraz/steadygate/releases/latest/download/<name>`.
- The release API used by the site is exactly
  `https://api.github.com/repos/Mesmoraz/steadygate/releases/latest`.
- `ACTION_REF = "v1"`. Generated workflows use `Mesmoraz/steadygate@v1`.
- Windows install directory: `%LOCALAPPDATA%\Programs\steadygate`. Mac and Linux:
  `~/.local/bin`.
- POSIX profile marker, exactly: `# added by steadygate-setup`.
- The generated config always contains `store: .steadygate/history.db`, `window: 10` and
  `min_history: 3`.
- Site copy that must not change: the hero headline "Your eval passed. Your agent got worse."
  and the footer "Runs entirely in your browser. Nothing is sent anywhere."
- The "not for" line, exactly: "If your tests give the same answer every time, a plain
  pass/fail is all you need."
- Never push tags or create releases while executing this plan. Rollout (Task 9) waits for
  the user's go-ahead.

## Review Focus

1. **A Windows user `Path` stored as `REG_EXPAND_SZ` with `%USERPROFILE%` entries.** Rewriting
   it must keep the type and leave the `%…%` entries unexpanded, otherwise the user's PATH
   is silently damaged. Tested in Task 4.
2. **A home folder with spaces or non-ASCII characters** (`C:\Users\Ana María\`,
   `/home/jo smith`). Installing and the profile line must still work, and the POSIX line
   must quote `$HOME`. Tested in Task 4.
3. **A results path typed with Windows backslashes** (`out\results.json`). The generated
   workflow runs on Linux, so it must contain `out/results.json`. Tested in Task 1.
4. **A tolerance typed as "5" or "5%".** "5%" means 0.05. A bare "5" is rejected with a
   message suggesting 0.05, rather than becoming a 500% tolerance. Tested in Task 3.
5. **The chosen project folder isn't a git repository.** Files are still written, and the
   wizard shows a warning, not an error. Tested in Task 3.

---

### Task 1: Scaffolding module

**Files:**
- Create: `src/steadygate/scaffold.py`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Produces:
  - `Format = Literal["promptfoo", "pytest-json", "junit", "jsonl"]` and
    `FORMATS: tuple[Format, ...]` (in that order)
  - `ACTION_REF = "v1"`, `CONFIG_PATH = Path("steadygate.yml")` and
    `WORKFLOW_PATH = Path(".github/workflows/steadygate.yml")`
  - `@dataclass(frozen=True) Answers(format: Format, results_path: str, metric: str,
    tolerance: float = 0.05, eval_command: str = "")`
  - `@dataclass(frozen=True) PlannedFile(path: Path, content: str, exists: bool)`. `path`
    is absolute.
  - `default_answers(format: Format) -> Answers`
  - `render_config(a: Answers) -> str`
  - `render_workflow(a: Answers) -> str`
  - `plan_files(repo: Path, a: Answers) -> list[PlannedFile]`, returning the config first
    and the workflow second
  - `write_files(planned: list[PlannedFile], overwrite: set[Path]) -> list[Path]`, which
    returns the paths written

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("fmt", FORMATS)
def test_rendered_config_loads(tmp_path, fmt):
    p = tmp_path / "steadygate.yml"
    p.write_text(render_config(default_answers(fmt)), encoding="utf-8")
    cfg = config.load(p)
    assert cfg.window == 10 and cfg.min_history == 3
    assert cfg.rules[0].metric == default_answers(fmt).metric

def test_defaults_per_format():
    assert default_answers("promptfoo") == Answers("promptfoo", "results.json", "score", 0.05,
        "npx promptfoo eval --repeat 5 --output results.json")
    assert default_answers("pytest-json").metric == "passed"
    assert default_answers("junit").results_path == "junit.xml"
    assert default_answers("jsonl") == Answers("jsonl", "results.jsonl", "accuracy", 0.05, "")

def test_pass_rate_min_is_one_minus_tolerance():
    raw = yaml.safe_load(render_config(Answers("junit", "junit.xml", "passed", 0.1)))
    assert raw["rules"][0] == {"type": "pass_rate", "metric": "passed", "min": 0.9, "scope": "case"}

def test_workflow_uses_action_ref_and_results():
    wf = yaml.safe_load(render_workflow(default_answers("promptfoo")))
    steps = wf["jobs"]["gate"]["steps"]
    gate = next(s for s in steps if "uses" in s and s["uses"].startswith("Mesmoraz/steadygate"))
    assert gate["uses"] == "Mesmoraz/steadygate@v1"
    assert gate["with"]["results"] == "results.json"
    assert any(s.get("run") == "npx promptfoo eval --repeat 5 --output results.json" for s in steps)

def test_workflow_without_eval_command_has_placeholder_comment():
    text = render_workflow(default_answers("jsonl"))
    assert "# TODO: run your evals here" in text
    yaml.safe_load(text)  # still valid

def test_backslash_results_path_is_normalised():  # Review Focus 3
    a = Answers("jsonl", "out\\results.jsonl", "accuracy")
    assert "out/results.jsonl" in render_workflow(a)
    assert "\\" not in render_workflow(a)

def test_plan_marks_existing(tmp_path):
    (tmp_path / "steadygate.yml").write_text("old", encoding="utf-8")
    planned = plan_files(tmp_path, default_answers("jsonl"))
    assert [p.exists for p in planned] == [True, False]
    assert planned[1].path == tmp_path / ".github" / "workflows" / "steadygate.yml"

def test_write_never_overwrites_unless_listed(tmp_path):
    (tmp_path / "steadygate.yml").write_text("old", encoding="utf-8")
    planned = plan_files(tmp_path, default_answers("jsonl"))
    written = write_files(planned, overwrite=set())
    assert (tmp_path / "steadygate.yml").read_text(encoding="utf-8") == "old"
    assert written == [planned[1].path]
    assert write_files(planned, overwrite={planned[0].path}) == [p.path for p in planned]
```

- [ ] **Step 2: Run the tests and check they fail**

Run: `pytest tests/test_scaffold.py -v`
Expected: FAIL with `ModuleNotFoundError: steadygate.scaffold`.

- [ ] **Step 3: Implement `scaffold.py`**

- **The rule depends on the format.** promptfoo and jsonl get `relative_delta` with
  `max_regression: tolerance`. pytest-json and junit get `pass_rate` with
  `min: round(1 - tolerance, 4)` and `scope: case`.
- **Write the YAML as text, not with `yaml.dump`,** so each rule gets a one-line comment in
  plain words, for example `# The mean score may not drop more than 5% below recent runs.`
- **The workflow** is the site's current snippet, with these changes:
  - `uses: Mesmoraz/steadygate@{ACTION_REF}`
  - `results:` set to the normalised results path (`replace("\\", "/")`)
  - the eval step set to `run: <eval_command>`, or, when the command is empty, to
    `run: echo "Run your evals here"` with the comment `# TODO: run your evals here` above it
- `write_files` creates parent folders and writes UTF-8 with `\n` line endings.

- [ ] **Step 4: Run the tests and check they pass**

Run: `pytest tests/test_scaffold.py -v`
Expected: all PASS. Then run `ruff check . && mypy`, which should be clean.

- [ ] **Step 5: Commit**

```bash
git add src/steadygate/scaffold.py tests/test_scaffold.py
git commit -m "feat: scaffold steadygate.yml and the workflow from a few answers"
```

---

### Task 2: `steadygate init`

**Files:**
- Modify: `src/steadygate/cli.py` (in `build_parser`, add an `init` subparser; in `main`,
  dispatch to `_init` before the `run_id` line, the same way `serve` is handled)
- Test: `tests/test_cli_init.py`

**Interfaces:**
- Consumes: everything from Task 1.
- Produces: `steadygate init [--dir PATH] [--format FMT] [--results PATH] [--metric NAME]
  [--tolerance FLOAT] [--eval-command CMD] [--force]`. Exit codes: 0 on success, 3 on bad
  input.

- [ ] **Step 1: Write the failing tests**

```python
def test_init_with_flags_writes_both(tmp_path, capsys):
    assert main(["init", "--dir", str(tmp_path), "--format", "junit"]) == 0
    assert (tmp_path / "steadygate.yml").exists()
    assert (tmp_path / ".github/workflows/steadygate.yml").exists()
    out = capsys.readouterr().out
    assert "steadygate.yml" in out and "git add" in out

def test_init_keeps_existing_without_force(tmp_path, capsys):
    (tmp_path / "steadygate.yml").write_text("old", encoding="utf-8")
    assert main(["init", "--dir", str(tmp_path), "--format", "jsonl"]) == 0
    assert (tmp_path / "steadygate.yml").read_text(encoding="utf-8") == "old"
    assert "kept existing" in capsys.readouterr().out
    assert main(["init", "--dir", str(tmp_path), "--format", "jsonl", "--force"]) == 0
    assert (tmp_path / "steadygate.yml").read_text(encoding="utf-8") != "old"

def test_init_without_format_and_no_tty_is_config_error(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["init", "--dir", str(tmp_path)]) == 3

def test_init_rejects_bad_tolerance(tmp_path):
    assert main(["init", "--dir", str(tmp_path), "--format", "jsonl", "--tolerance", "5"]) == 3

def test_init_interactive(tmp_path, monkeypatch):
    class TTY(io.StringIO):
        def isatty(self) -> bool: return True
    monkeypatch.setattr("sys.stdin", TTY(""))
    answers = iter(["promptfoo", "", "", "", ""])  # format, then accept the defaults
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main(["init", "--dir", str(tmp_path)]) == 0
    assert "score" in (tmp_path / "steadygate.yml").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the tests and check they fail**

Run: `pytest tests/test_cli_init.py -v`
Expected: FAIL (`invalid choice: 'init'`, which is argparse's exit 2).

- [ ] **Step 3: Implement `_init(args: argparse.Namespace) -> int` in `cli.py`**

- **Tolerance:** the valid range is 0 < t < 1. Anything else goes through
  `_config_error("tolerance must be between 0 and 1, e.g. 0.05 for 5%")`.
- **Interactive prompts** run only when `sys.stdin.isatty()` and `--format` is missing.
  - The format prompt lists `FORMATS`.
  - The prompts for results path, metric, tolerance and eval command show the defaults in
    brackets, and an empty answer keeps the default.
- **Output:** print `wrote <path>` or `kept existing <path> (use --force to replace)` for
  each file. Then print the next steps: `git add steadygate.yml .github/workflows/steadygate.yml`
  and "push to main to start building history".

- [ ] **Step 4: Run the tests and check they pass**

Run: `pytest tests/test_cli_init.py tests/test_cli.py -v`
Expected: all PASS. Then run `pytest`; coverage should still be at least 90%.

- [ ] **Step 5: Commit**

```bash
git add src/steadygate/cli.py tests/test_cli_init.py
git commit -m "feat(cli): steadygate init writes a starter config and workflow"
```

---

### Task 3: Wizard state

**Files:**
- Create: `installer/__init__.py` (empty), `installer/state.py`
- Modify: `pyproject.toml`. Change `[tool.mypy] files` to `["src", "site", "installer"]`
  and add the extra `installer = ["pyinstaller>=6.10"]`.
- Test: `tests/test_installer_state.py`. Tests import `installer.state`, so add
  `pythonpath = ["."]` to `[tool.pytest.ini_options]`.

**Interfaces:**
- Consumes: `Answers`, `Format`, `default_answers`, `plan_files` and `PlannedFile` from Task 1.
- Produces:
  - `class Page(Enum): WELCOME, LOCATION, PROJECT, DONE`
  - `parse_tolerance(text: str) -> float`, which raises `ValueError` with a message
  - `@dataclass WizardState` with these fields:
    - `page: Page = Page.WELCOME`
    - `install_dir: Path`
    - `add_to_path: bool = True`
    - `installed: InstallResult | None = None` (the type comes from Task 4; use a forward
      reference under `TYPE_CHECKING`)
    - `setup_project: bool = False`
    - `repo: Path | None = None`
    - `format: Format = "promptfoo"`
    - `results_path: str`, `metric: str`, `tolerance_text: str` and `eval_command: str`,
      filled from `default_answers`
    - `overwrite: set[Path]`
    - `written: list[Path]`
  - Methods:
    - `set_format(fmt: Format) -> None` refills `results_path`, `metric`, `tolerance_text`
      and `eval_command` from the defaults.
    - `errors() -> dict[str, str]` maps a field name to a message. It only checks the
      fields on the current page.
    - `warnings() -> list[str]`
    - `answers() -> Answers`
    - `planned() -> list[PlannedFile]`
    - `can_go_next() -> bool`, `next() -> None` and `back() -> None`

- [ ] **Step 1: Write the failing tests**

```python
def state(**kw): return WizardState(install_dir=Path("/x"), **kw)

def test_page_order_and_install_gate():
    s = state()
    s.next(); assert s.page is Page.LOCATION
    assert not s.can_go_next()            # must install first
    s.installed = object()                # any non-None result
    s.next(); assert s.page is Page.PROJECT
    s.back(); assert s.page is Page.LOCATION

def test_project_page_skipped_when_unchecked_goes_to_done():
    s = state(page=Page.PROJECT, setup_project=False)
    assert s.can_go_next(); s.next(); assert s.page is Page.DONE

def test_project_errors(tmp_path):
    s = state(page=Page.PROJECT, setup_project=True)
    assert s.errors()["repo"] == "Choose a folder."
    s.repo = tmp_path; s.metric = ""
    assert s.errors() == {"metric": "Enter the metric name to gate on."}

@pytest.mark.parametrize("text,value", [("0.05", 0.05), ("5%", 0.05), ("12.5%", 0.125)])
def test_tolerance_accepts(text, value):  # Review Focus 4
    assert parse_tolerance(text) == pytest.approx(value)

@pytest.mark.parametrize("text", ["5", "0", "1", "-0.1", "abc", ""])
def test_tolerance_rejects(text):
    with pytest.raises(ValueError, match="0.05"):
        parse_tolerance(text)

def test_set_format_refills_defaults():
    s = state(); s.metric = "custom"
    s.set_format("junit")
    assert (s.metric, s.results_path) == ("passed", "junit.xml")

def test_non_git_folder_is_a_warning_not_an_error(tmp_path):  # Review Focus 5
    s = state(page=Page.PROJECT, setup_project=True, repo=tmp_path)
    assert s.errors() == {}
    assert s.warnings() == ["This folder isn't a git repository. The files will still be written."]
```

- [ ] **Step 2: Run the tests and check they fail**

Run: `pytest tests/test_installer_state.py -v`
Expected: FAIL (no `installer.state`).

- [ ] **Step 3: Implement `installer/state.py`**

- The `parse_tolerance` error message is exactly:
  `"Enter a fraction between 0 and 1, e.g. 0.05 for 5%."`
- A trailing `%` divides the number by 100.
- The git check is `(repo / ".git").exists()`.

- [ ] **Step 4: Run the tests and check they pass**

Run: `pytest tests/test_installer_state.py -v && mypy && ruff check .`
Expected: PASS, and both linters clean.

- [ ] **Step 5: Commit**

```bash
git add installer/__init__.py installer/state.py tests/test_installer_state.py pyproject.toml
git commit -m "feat(installer): wizard page and answer logic"
```

---

### Task 4: Install and PATH logic

**Files:**
- Create: `installer/install.py`
- Test: `tests/test_installer_install.py`

**Interfaces:**
- Produces:
  - `MARKER = "# added by steadygate-setup"`
  - `class InstallError(Exception)`
  - `class Registry(Protocol)` with two methods:
    - `get_user_path(self) -> tuple[str, int] | None`, which returns `(value, reg_type)`
    - `set_user_path(self, value: str, reg_type: int) -> None`
  - `class WinRegistry`, the real implementation. It uses `winreg` on
    `HKEY_CURRENT_USER\Environment`, and after writing it broadcasts `WM_SETTINGCHANGE`
    through `ctypes.windll.user32.SendMessageTimeoutW`. It is only imported on Windows.
  - `default_install_dir(os_name: str, env: Mapping[str, str], home: Path) -> Path`, where
    `os_name` is `"windows"`, `"macos"` or `"linux"`
  - `install_binary(src: Path, dest_dir: Path) -> Path`
  - `add_to_windows_path(directory: Path, reg: Registry) -> bool`
  - `add_to_posix_path(directory: Path, home: Path, env_path: str, os_name: str) -> list[Path]`
  - `@dataclass InstallResult(binary: Path, path_changed: bool, profiles: list[Path])`
  - `run_install(src: Path, dest_dir: Path, add_to_path: bool, *, os_name: str,
    env: Mapping[str, str], home: Path, registry: Registry | None) -> InstallResult`

- [ ] **Step 1: Write the failing tests**

```python
REG_SZ, REG_EXPAND_SZ = 1, 2

class FakeRegistry:
    def __init__(self, value=None): self.value = value
    def get_user_path(self): return self.value
    def set_user_path(self, value, reg_type): self.value = (value, reg_type)

def test_default_dirs(tmp_path):
    assert default_install_dir("windows", {"LOCALAPPDATA": r"C:\L"}, tmp_path) == Path(r"C:\L") / "Programs" / "steadygate"
    assert default_install_dir("linux", {}, tmp_path) == tmp_path / ".local" / "bin"

def test_install_binary_copies_and_is_executable(tmp_path):
    src = tmp_path / "steadygate"; src.write_bytes(b"bin")
    dest = install_binary(src, tmp_path / "Ana María" / "bin")  # Review Focus 2
    assert dest.read_bytes() == b"bin"
    if os.name != "nt": assert dest.stat().st_mode & 0o111

def test_windows_path_keeps_expand_sz_and_unexpanded_entries():  # Review Focus 1
    reg = FakeRegistry((r"%USERPROFILE%\bin;C:\tools", REG_EXPAND_SZ))
    assert add_to_windows_path(Path(r"C:\L\Programs\steadygate"), reg) is True
    assert reg.value == (r"%USERPROFILE%\bin;C:\tools;C:\L\Programs\steadygate", REG_EXPAND_SZ)

def test_windows_path_already_present_is_noop():
    reg = FakeRegistry((r"c:\l\programs\steadygate\;C:\tools", REG_SZ))
    assert add_to_windows_path(Path(r"C:\L\Programs\steadygate"), reg) is False

def test_windows_path_empty_or_missing():
    reg = FakeRegistry(None)
    add_to_windows_path(Path(r"C:\x"), reg)
    assert reg.value == (r"C:\x", REG_EXPAND_SZ)

def test_posix_profile_added_once_and_quoted(tmp_path):  # Review Focus 2
    home = tmp_path / "jo smith"; home.mkdir()
    d = home / ".local" / "bin"
    changed = add_to_posix_path(d, home, "/usr/bin", "linux")
    assert changed == [home / ".profile"]
    assert add_to_posix_path(d, home, "/usr/bin", "linux") == []
    text = (home / ".profile").read_text()
    assert text.count(MARKER) == 1
    assert 'export PATH="$HOME/.local/bin:$PATH"' in text

def test_posix_macos_also_updates_existing_bash_profile(tmp_path):
    (tmp_path / ".bash_profile").write_text("# mine\n")
    changed = add_to_posix_path(tmp_path / ".local/bin", tmp_path, "/usr/bin", "macos")
    assert changed == [tmp_path / ".zprofile", tmp_path / ".bash_profile"]

def test_posix_already_on_path_is_noop(tmp_path):
    d = tmp_path / ".local/bin"
    assert add_to_posix_path(d, tmp_path, f"/usr/bin:{d}", "linux") == []

def test_locked_binary_raises_friendly_error(tmp_path, monkeypatch):
    src = tmp_path / "s"; src.write_bytes(b"x")
    monkeypatch.setattr(shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(InstallError, match="close any terminal running steadygate"):
        install_binary(src, tmp_path / "d")
```

- [ ] **Step 2: Run the tests and check they fail**

Run: `pytest tests/test_installer_install.py -v`
Expected: FAIL (no `installer.install`).

- [ ] **Step 3: Implement `installer/install.py`**

- **Windows "already present" check:** compare entries case-insensitively, ignoring
  trailing `\`.
- **Windows write:** append with `;` and keep the original `reg_type`. When there is no
  value yet, use `REG_EXPAND_SZ` (2).
- **POSIX profile line:** when `home` is the profile's home, write `$HOME/...`; otherwise
  write the absolute path, double-quoted.
- **POSIX "already present" check:** compare `env_path` entries after `Path.resolve()`.
- **`install_binary`:** use `shutil.copy2` and then `chmod 0o755`. Catch `PermissionError`
  and raise `InstallError("Couldn't replace steadygate. Please close any terminal running
  steadygate and try again.")`.

- [ ] **Step 4: Run the tests and check they pass**

Run: `pytest tests/test_installer_install.py -v && mypy`
Expected: PASS. mypy must be clean on the non-Windows CI runners too, so guard the
`winreg` and `ctypes.windll` imports with `if sys.platform == "win32":`.

- [ ] **Step 5: Commit**

```bash
git add installer/install.py tests/test_installer_install.py
git commit -m "feat(installer): copy the CLI and add it to PATH per OS"
```

---

### Task 5: Tk wizard, entry points and local build

**Files:**
- Create: `installer/wizard.py`, `installer/cli_entry.py`, `installer/build.py`,
  `installer/smoke.sh`
- Modify: `.gitignore` (add `build/`, `dist-installer/` and `*.spec`)
- Test: `tests/test_installer_wizard.py` (headless parts only)

**Interfaces:**
- Consumes: `WizardState` and `Page` (Task 3); `run_install`, `default_install_dir`,
  `WinRegistry` and `InstallError` (Task 4); `write_files` (Task 1).
- Produces:
  - `installer/wizard.py`:
    - `embedded_cli() -> Path` returns `Path(sys._MEIPASS) / "bin" / ("steadygate.exe" on
      Windows else "steadygate")`. When it isn't frozen, it falls back to the
      `STEADYGATE_CLI` environment variable.
    - `self_check() -> int` prints `steadygate-setup: cli=<path> version=<stdout of
      cli --version>` and returns 0. It returns 1 if the CLI is missing or fails.
    - `main(argv: Sequence[str] | None = None) -> int`
  - `installer/build.py`: `python installer/build.py --out dist-installer` produces
    `dist-installer/steadygate-setup-{windows.exe|macos.zip|linux}` and
    `dist-installer/steadygate[.exe]`, and prints both paths.
  - `installer/smoke.sh <cli-binary>` exits 0 only when the `examples/basic` flow returns
    the exit codes 0, 1 and 2 in the right places.

- [ ] **Step 1: Write the failing tests**

```python
def test_self_check_reports_cli(tmp_path, monkeypatch, capsys):
    fake = tmp_path / ("steadygate.cmd" if os.name == "nt" else "steadygate")
    fake.write_text("@echo steadygate 0.0.1\n" if os.name == "nt" else "#!/bin/sh\necho steadygate 0.0.1\n")
    fake.chmod(0o755)
    monkeypatch.setenv("STEADYGATE_CLI", str(fake))
    assert wizard.self_check() == 0
    assert "version=steadygate 0.0.1" in capsys.readouterr().out

def test_self_check_missing_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("STEADYGATE_CLI", str(tmp_path / "nope"))
    assert wizard.self_check() == 1

def test_main_self_check_flag(monkeypatch):
    monkeypatch.setattr(wizard, "self_check", lambda: 0)
    assert wizard.main(["--self-check"]) == 0

def test_no_display_points_to_init(monkeypatch, capsys):
    def boom(): raise tkinter.TclError("no display name")
    monkeypatch.setattr(wizard, "_run_gui", boom)
    assert wizard.main([]) == 1
    assert "steadygate init" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests and check they fail**

Run: `pytest tests/test_installer_wizard.py -v`
Expected: FAIL (no `installer.wizard`).

- [ ] **Step 3: Implement `installer/wizard.py`**

- `_run_gui() -> None` builds a `tk.Tk` window titled "Steadygate setup", 560×420 and not
  resizable, using `ttk` widgets. There is one frame per `Page`, and *Back*, *Next* and
  *Cancel* are in a bottom bar. Next and Back call the matching `WizardState` methods and
  then re-render the frame.
- **Welcome page:** the heading "Set up Steadygate", then the sentence "Steadygate tells you
  whether a new version of your AI feature or flaky test suite is safe to ship, compared
  with your own history." Then these three lines:
  - "For AI and LLM app teams running evals"
  - "For QA and release owners signing off releases"
  - "For teams with noisy, flaky tests"
- **Location page:**
  - the install folder in an entry, with a *Change…* button (`filedialog.askdirectory`)
  - the checkbox "Add steadygate to my PATH"
  - an *Install* button that calls `run_install` with `os_name` taken from `sys.platform`
  - the result or the `InstallError` message shown in a label, with *Install* relabelled
    *Retry* after an error
- **Project page:**
  - the checkbox "Set up a repository now"
  - a folder picker, a format `Combobox` bound to `set_format`, and entries for results
    file, metric, tolerance and eval command
  - a red `errors()` label under each field and the `warnings()` text in amber
  - the list of files from `planned()`, each existing one with an "Overwrite" checkbox that
    is off by default
  - *Next* calls `write_files(state.planned(), state.overwrite)` and stores the result in
    `state.written`
- **Done page:** a list of what was installed and written, a read-only `Text` with the next
  steps (`steadygate --version`, then the `git add …` line, then "push to main"), and a link
  button to `https://mesmoraz.github.io/steadygate/` opened with `webbrowser.open`.
- **`main`:** handles `--self-check`. Otherwise it calls `_run_gui()` and catches
  `tkinter.TclError`. On that error it prints to stderr: "steadygate-setup needs a desktop
  session. In a terminal, run: steadygate init" and returns 1.

- [ ] **Step 4: Run the tests and check they pass**

Run: `pytest tests/test_installer_wizard.py -v && mypy && ruff check .`
Expected: PASS.

- [ ] **Step 5: Implement `installer/cli_entry.py`, `installer/build.py` and `installer/smoke.sh`**

- **`cli_entry.py`:** `from steadygate.cli import main; raise SystemExit(main())`
- **`build.py`, CLI build:** `pyinstaller --onefile --name steadygate --collect-submodules
  steadygate --distpath <out> installer/cli_entry.py`
- **`build.py`, wizard build:** `pyinstaller --onefile --windowed --name steadygate-setup
  --add-data <out>/steadygate[.exe]{os.pathsep}bin --distpath <out>/setup
  installer/wizard.py`. On Linux, omit `--windowed`.
- **`build.py`, rename and package:**
  - Windows: rename the wizard to `steadygate-setup-windows.exe`.
  - Linux: rename it to `steadygate-setup-linux`.
  - macOS: zip the `.app` with `ditto -c -k --keepParent` to `steadygate-setup-macos.zip`.
- **`smoke.sh`:** use `set -u`. `cd` into a temporary copy of `examples/basic`, record
  `baseline-1..3`, then assert these exit codes: `run candidate-noise.jsonl` gives 0,
  `candidate-regression.jsonl` gives 1, and `candidate-broken.jsonl` gives 2. Each assertion
  is `"$BIN" run … ; test $? -eq N || { echo "expected N"; exit 1; }`.

- [ ] **Step 6: Build locally and check it**

Run: `pip install -e ".[installer]" && python installer/build.py --out dist-installer`

Then:
- `bash installer/smoke.sh dist-installer/steadygate.exe`, which should exit 0.
- `dist-installer/steadygate-setup-windows.exe --self-check`, which should print
  `version=steadygate 0.0.1`.

PyInstaller's `--windowed` hides stdout on Windows, so in `self_check` attach to the parent
console with `ctypes.windll.kernel32.AttachConsole(-1)` before printing.

- [ ] **Step 7: Run the wizard by hand on this machine**

1. Double-click `dist-installer/steadygate-setup-windows.exe`.
2. Install to a temporary folder with the PATH box *unchecked*.
3. Set up a project in a scratch folder.
4. Check that the two files exist and that `config.load` accepts the YAML.
5. Take a screenshot of each page for the PR.

Don't tick "Add to PATH" on the user's machine unless they say so.

- [ ] **Step 8: Commit**

```bash
git add installer/ tests/test_installer_wizard.py .gitignore
git commit -m "feat(installer): windowed setup wizard and PyInstaller build"
```

---

### Task 6: Release workflow

**Files:**
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: `installer/build.py` and `installer/smoke.sh` (Task 5).
- Produces: a GitHub Release with the three fixed asset names, and a `v1` tag moved to the
  release commit.

- [ ] **Step 1: Write the workflow**

- `on: push: tags: ["v*"]` and `workflow_dispatch`
- `permissions: contents: read` at the top level
- **Job `build`:**
  - `strategy.matrix.os: [windows-latest, macos-latest, ubuntu-22.04]`, with
    `fail-fast: false`
  - Steps:
    1. checkout
    2. `setup-python` 3.12
    3. `pip install ".[installer]"`
    4. `python installer/build.py --out dist-installer`
    5. `bash installer/smoke.sh dist-installer/steadygate*` (use `shell: bash` everywhere)
    6. the self-check: on Windows and Linux run the setup binary with `--self-check`; on
       macOS run
       `unzip -q dist-installer/steadygate-setup-macos.zip -d x && x/steadygate-setup.app/Contents/MacOS/steadygate-setup --self-check`
    7. `upload-artifact` named `setup-${{ runner.os }}`, containing only the
       `steadygate-setup-*` file
- **Job `release`:**
  - `needs: build`, `if: startsWith(github.ref, 'refs/tags/v')`,
    `permissions: contents: write`
  - Steps:
    1. checkout
    2. `download-artifact` with `merge-multiple: true` into `assets/`
    3. `gh release create "$GITHUB_REF_NAME" assets/* --generate-notes --title "$GITHUB_REF_NAME"`
    4. `git tag -f v1 "$GITHUB_SHA" && git push -f origin v1`

- [ ] **Step 2: Check the workflow parses**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml'))"`
Expected: no error. If `actionlint` is installed, also run `actionlint`, which should be
clean.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: build and publish the setup wizard for Windows, Mac and Linux on tags"
```

The real run (`workflow_dispatch` on the pushed branch) is part of Task 9.

---

### Task 7: Site install script and copy

**Files:**
- Create: `site/install.js`
- Modify: `site/index.html` (hero CTA; new `#who` section after the hero; `id="install"`
  plus the downloads on the existing install section; new `#trust` strip before the
  footer), `site/style.css`, and `site/build.py` (add `"install.js"` to `SITE_FILES`)
- Test: `tests/test_site_js.py` (add install tests), `tests/test_site_build.py` (add checks)

**Interfaces:**
- Produces, in `site/install.js` (`module.exports` when there is no `window`):
  - `ASSETS = {windows: "steadygate-setup-windows.exe", macos: "steadygate-setup-macos.zip", linux: "steadygate-setup-linux"}`
  - `LABELS = {windows: "Windows", macos: "Mac", linux: "Linux"}`
  - `detectOS({platform, userAgent}) -> "windows"|"macos"|"linux"|null`
  - `downloadUrl(os) -> string`
  - `RELEASE_API`
  - `initInstall(doc, fetchFn)`, which sets the button's href and label, then sends the
    release check: on a 404 it swaps in the "Coming soon" text, and on any other failure it
    changes nothing

- [ ] **Step 1: Write the failing tests**

```python
INSTALL_JS = REPO / "site" / "install.js"

@pytest.mark.parametrize("platform,ua,expected", [
    ("Windows", "", "windows"),
    ("", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "windows"),
    ("macOS", "", "macos"),
    ("", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5)", "macos"),
    ("Linux", "", "linux"),
    ("", "Mozilla/5.0 (X11; Linux x86_64)", "linux"),
    ("", "Mozilla/5.0 (Linux; Android 14)", None),
    ("", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", None),
])
def test_detect_os(platform, ua, expected):
    assert node(INSTALL_JS, f"m.detectOS({json.dumps({'platform': platform, 'userAgent': ua})})") == expected

def test_download_url():
    assert node(INSTALL_JS, 'm.downloadUrl("macos")') == \
        "https://github.com/Mesmoraz/steadygate/releases/latest/download/steadygate-setup-macos.zip"
```

In `tests/test_site_build.py`:

```python
def test_site_has_install_and_audience(tmp_path, build):
    html = (REPO / "site" / "index.html").read_text(encoding="utf-8")
    for name in ("steadygate-setup-windows.exe", "steadygate-setup-macos.zip", "steadygate-setup-linux"):
        assert f"releases/latest/download/{name}" in html
    assert 'id="who"' in html and 'id="install"' in html and 'id="trust"' in html
    assert "If your tests give the same answer every time, a plain pass/fail is all you need." in html
    assert "install.js" in build.SITE_FILES
```

- [ ] **Step 2: Run the tests and check they fail**

Run: `pytest tests/test_site_js.py tests/test_site_build.py -v`
Expected: the new tests FAIL.

- [ ] **Step 3: Implement `install.js`**

- `detectOS` rejects Android and iPhone/iPad first, then checks `platform` and falls back
  to the user agent.
- `initInstall` looks for `#install-now` (an `<a>`) and `#install-note`.
- **The button's text:**
  - when the OS is known: "Install for {LABELS[os]}", linking to `downloadUrl(os)`
  - otherwise: "Install now", linking to `#install`
- **On a 404,** replace the button with
  `Coming soon. For now: <code>pip install git+https://github.com/Mesmoraz/steadygate</code>`.

- [ ] **Step 4: Update the page**

- **Hero CTA:** `<a id="install-now" class="btn btn-primary" href="#install">Install now</a>`,
  then *Try the demo* (`#demo`) and *View on GitHub*. Under them,
  `<p class="install-note" id="install-note">`, containing:
  - "Other platforms" links (`#install`)
  - this note: "Windows may say 'Windows protected your PC': choose More info → Run anyway.
    On a Mac, right-click the app and choose Open."
- **`#who` section:** three `.explain-card` cards with the spec §6.2 copy, each with
  "You're in the right place if…" and "What you get" lines. Then the "not for" line from
  Global Constraints.
- **`#install` section:** start it with the three download links, labelled Windows, Mac and
  Linux, plus `pip install git+https://github.com/Mesmoraz/steadygate` and
  `steadygate init`. Then keep the existing workflow snippet unchanged (it stays on `@main`
  until rollout).
- **`#trust` strip:** five short points from spec §6.3.
- **Scripts:** add `<script src="install.js"></script>` before `app.js`, and call
  `initInstall(document, fetch)` on `DOMContentLoaded` in `install.js` itself when `window`
  exists.
- **Styles:** reuse the existing `.explain`, `.explain-card` and `.btn` styles. New CSS is
  only for `.install-note`, `.downloads` and `.trust`, using the existing custom properties.
  Check the layout at 360 px.

- [ ] **Step 5: Run the tests and check they pass**

Run: `pytest tests/test_site_js.py tests/test_site_build.py -v`
Expected: PASS.

- [ ] **Step 6: Check it in the browser**

1. Run `python site/build.py --out _site`, then serve `_site` through a `.claude/launch.json`
   entry running `python -m http.server 8000 -d _site`.
2. Use `preview_start`.
3. `read_page` and confirm the button reads "Install for Windows", and check the console
   for errors.
4. Before the first release, the 404 path should show "Coming soon".
5. Resize to mobile and to dark mode, and take a screenshot of each.

- [ ] **Step 7: Commit**

```bash
git add site/ tests/test_site_js.py tests/test_site_build.py
git commit -m "feat(site): install button, who it's for, and why you can trust it"
```

---

### Task 8: README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Edit the README**

- **Add "## Who it's for"** after the exit-code table: the three audiences from spec §6.2,
  one bullet each, and the "not for" line.
- **Quick start:** start with "Download the installer" and the three
  `releases/latest/download/…` links, with the one-line unsigned-build note. Then
  `steadygate init`. Keep `pip install git+…` as "Or install with pip".
- **Add "## Uninstall"** with the steps for each OS:
  - delete the binary from the install folder
  - Windows: remove the folder from the user PATH in *Edit environment variables for your
    account*
  - Mac and Linux: delete the line that ends `# added by steadygate-setup` from
    `~/.zprofile`, `~/.bash_profile` or `~/.profile`

- [ ] **Step 2: Check it**

Run: `pytest` (the whole suite) and `ruff check . && ruff format --check . && mypy`
Expected: all green, with coverage at least 90%.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: who it's for, installer download and uninstall steps"
```

---

### Task 9: Cross-OS build check and rollout (needs the user's go-ahead)

- [ ] **Step 1:** Ask the user before pushing the branch. Once they agree:

```bash
git push -u origin installer-and-audience
gh workflow run release.yml --ref installer-and-audience
```

  All three matrix jobs must pass the smoke test and the self-check. Download the macOS
  and Linux artifacts and confirm their names.
- [ ] **Step 2:** Open the PR with screenshots of the wizard (Task 5, Step 7) and of the
  site (Task 7, Step 6). Bind it with the ccd_pr tools.
- [ ] **Step 3:** After it merges, *ask the user* before pushing `v0.0.1`. The release job
  publishes the three files and moves `v1`.
- [ ] **Step 4:** Confirm each `releases/latest/download/<name>` URL returns 302 to a file.
  Then, in one small follow-up commit:
  - switch the site and README workflow snippets to `Mesmoraz/steadygate@v1`
  - test that the live site's button now reads "Install for Windows" instead of
    "Coming soon"
