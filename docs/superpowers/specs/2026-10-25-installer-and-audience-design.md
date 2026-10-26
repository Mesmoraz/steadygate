# Steadygate Installer Wizard and "Who It's For" — Design

**Date:** 2026-10-25
**Status:** Draft, awaiting review

## 1. Intent

A visitor to the site should understand **who Steadygate is for and why they'd want it**
within a few seconds of landing. If it fits, they should be able to press **Install now**,
get a download for their OS, and finish with the `steadygate` command on their PATH and a
working `steadygate.yml` and GitHub workflow in their repo. They should not need Python or a
terminal for any of it.

**Success:**
- Each of the four audiences can find a line written for them above the demo.
- On Windows, Mac and Linux, "Install now" downloads the right file without the user having
  to choose.
- After the wizard finishes, `steadygate --version` works in a new terminal, and the chosen
  repo contains a valid config and workflow.

**Said by the user:**
- The audiences are AI/LLM app teams, QA and release owners, teams with flaky tests, and
  hiring managers.
- The download is a compiled, **windowed** wizard hosted on their GitHub.
- It installs the CLI **and** sets up a project, on **Windows, Mac and Linux**.

**Assumed (accepted in design review):**
- Downloads are hosted on GitHub Releases.
- Builds are unsigned for now, and the site explains the SmartScreen and Gatekeeper warnings.
- The user won't need Python installed.

**Out of scope:**
- Code signing and notarisation.
- Auto-update.
- An uninstaller beyond the documented manual steps (§4.5).
- Installing the companion (`serve`) extras.
- Homebrew, winget and apt packages.
- Uploading to PyPI.

## 2. Approach

**Chosen:** two PyInstaller binaries per OS.
- `steadygate` is the CLI, built from the existing entry point.
- `steadygate-setup` is a Tkinter wizard that carries the CLI binary as bundled data and
  installs it.

The download button only ever hands out the setup file. The project-setup logic lives in the
library as pure functions, and a new `steadygate init` command uses the same functions.

**Rejected:**
- **One binary that changes behaviour.** It would open the wizard when double-clicked and act
  as the CLI in a terminal. Reliably telling the two apart on three operating systems is
  fragile.
- **A wizard that runs `pip install`.** This needs Python on the user's machine, which defeats
  the point of a compiled installer.

## 3. Project scaffolding (`src/steadygate/scaffold.py`)

Pure functions with no I/O apart from `write_files`.

```python
Format = Literal["promptfoo", "pytest-json", "junit", "jsonl"]

@dataclass(frozen=True)
class Answers:
    format: Format
    results_path: str          # e.g. "results.json"
    metric: str                # default depends on format, see table
    tolerance: float = 0.05    # max_regression for relative_delta, or 1 - min for pass_rate
    eval_command: str = ""     # put into the workflow; a commented placeholder when empty

def default_answers(format: Format) -> Answers
def render_config(a: Answers) -> str          # steadygate.yml text
def render_workflow(a: Answers) -> str        # .github/workflows/steadygate.yml text
def plan_files(repo: Path, a: Answers) -> list[PlannedFile]   # path, content, exists: bool
def write_files(planned: list[PlannedFile], overwrite: set[Path]) -> list[Path]
```

The defaults for each format follow what the existing adapters produce:

| Format | Default metric | Default rule | Default results path | Default eval command |
|---|---|---|---|---|
| promptfoo | `score` | `relative_delta`, `max_regression: tolerance` | `results.json` | `npx promptfoo eval --repeat 5 --output results.json` |
| pytest-json | `passed` | `pass_rate`, `min: 1 - tolerance`, `scope: case` | `report.json` | `pytest --json-report --json-report-file=report.json` |
| junit | `passed` | `pass_rate`, `min: 1 - tolerance`, `scope: case` | `junit.xml` | `pytest --junitxml=junit.xml` |
| jsonl | `accuracy` | `relative_delta`, `max_regression: tolerance` | `results.jsonl` | *(placeholder comment)* |

- `render_config` writes:
  - `store: .steadygate/history.db`, `window: 10` and `min_history: 3`
  - the rule, with a short comment above it explaining it in plain words
- `render_workflow` writes the same workflow shown on the site today, with `results:` and
  the eval step filled in, and uses `Mesmoraz/steadygate@v1`. Releases move a `v1` tag; see
  §5. The ref is a module constant, `ACTION_REF = "v1"`. The site's snippet moves from
  `@main` to `@v1` in rollout step 3.
- **Validation:** the config produced for every format must load through
  `steadygate.config.load_config` without error. A test enforces this.
- **Overwrites:** `write_files` only overwrites the paths listed in `overwrite`. It creates
  `.github/workflows/` if needed.

### `steadygate init`

```
steadygate init [--dir .] [--format promptfoo|pytest-json|junit|jsonl]
                [--results PATH] [--metric NAME] [--tolerance 0.05] [--force]
```

- **With no `--format`** it asks the same questions in the terminal, using `input()`. When
  stdin is not a TTY, a missing `--format` is a config error (exit 3).
- **Existing files** are listed and left alone unless `--force` is given. It prints what it
  wrote and the next commands to run.
- **Exit codes:** 0 on success, 3 on bad input.

## 4. Wizard (`installer/`)

### 4.1 Layout

```
installer/
  wizard.py         # Tk widgets only: builds pages, binds buttons to WizardState
  state.py          # WizardState: page order, answers, validation, no Tk import
  install.py        # copy the CLI binary, PATH changes per OS, no Tk import
  steadygate-cli.spec / steadygate-setup.spec   # PyInstaller specs
```

`installer/` is outside the wheel. `mypy` and `ruff` cover it, and `tests/test_installer_*.py`
tests `state.py` and `install.py` without a display.

### 4.2 Pages

1. **Welcome.** One sentence on what Steadygate does and three short "for you if…" lines.
   Buttons: *Next*, *Cancel*.
2. **Install location.** Shows the default folder with a *Change…* button, and a checkbox
   (on by default) for *Add steadygate to my PATH*. *Install* copies the CLI and shows
   progress and the result inline. Errors are shown in the page and *Retry* is offered;
   it never closes silently.
3. **Set up a project (optional).**
   - Checkbox: *Set up a repository now*. When unchecked, the fields are disabled.
   - When checked:
     - a folder picker
     - a format dropdown
     - results path, metric and tolerance, pre-filled from `default_answers` when the
       format changes
     - an optional eval command
   - It shows the files that will be written. Any file that already exists gets an
     *Overwrite* checkbox, off by default.
   - *Next* writes the files.
4. **Done.** Lists what was installed and written, then shows the next steps as copyable
   text:
   - open a new terminal
   - `steadygate --version`
   - commit the two files and push to `main` to start building history

   A link opens the live demo.

`WizardState` holds the current page, the answers, and the results of each step. It exposes
`can_go_next()`, `next()`, `back()` and `validate()`, which returns the messages to show for
each field.

### 4.3 Install paths (`install.py`)

| OS | Install directory | PATH change |
|---|---|---|
| Windows | `%LOCALAPPDATA%\Programs\steadygate\steadygate.exe` | Append to the **user** `Path` in `HKCU\Environment` with `winreg`, if it isn't already there. Then broadcast `WM_SETTINGCHANGE` so new terminals pick it up. No admin needed. |
| macOS | `~/.local/bin/steadygate` | If `~/.local/bin` isn't on PATH, append `export PATH="$HOME/.local/bin:$PATH"` to `~/.zprofile` (and to `~/.bash_profile` if it exists). Guarded by a `# added by steadygate-setup` marker so it is never added twice. |
| Linux | `~/.local/bin/steadygate` | Same as macOS, using `~/.profile`. |

- The binary is copied with `chmod 755` on POSIX.
- Reinstalling replaces the binary. On Windows, if the file is locked, it says so and asks
  the user to close terminals running `steadygate`.
- `install.py` takes the home directory, environment and a registry adapter as parameters,
  so tests can use a temporary directory and a fake registry.

### 4.4 Bundling

- The setup spec adds the freshly built CLI binary as data at `bin/steadygate[.exe]`.
- At runtime the wizard finds it through `sys._MEIPASS`.
- `--windowed` is used on Windows and macOS. On macOS the output is a `.app` bundle,
  zipped for download.

### 4.5 Uninstall (documented, not automated)

The README lists the steps for each OS:
- delete the binary
- remove the PATH entry, or the marked profile line

## 5. Release workflow (`.github/workflows/release.yml`)

- **Trigger:** `push` of tags matching `v*`, plus `workflow_dispatch`, which builds without
  publishing.
- **Build job:** runs on a matrix of `windows-latest`, `macos-latest` and `ubuntu-22.04`.
  `ubuntu-22.04` keeps the glibc baseline older than `ubuntu-latest`. Each machine:
  1. Sets up Python 3.12 and runs `pip install . pyinstaller`.
  2. Builds the CLI with PyInstaller (one file).
  3. **Smoke-tests the CLI:** `steadygate --version`, then the `examples/basic` record and
     run flow, checking for exit codes 0, 1 and 2.
  4. Builds the setup wizard with the CLI embedded.
  5. **Smoke-tests the wizard headless:** `steadygate-setup --self-check` prints the
     embedded CLI path and version, then exits without creating a window.
  6. Renames the output to its fixed name and uploads it as a workflow artifact.
- **Release job:** runs on tag pushes only and needs `contents: write`. It creates the
  GitHub Release with `gh release create`, attaches the three files, and generates notes.
  It then force-moves the `v1` tag to the release commit so `Mesmoraz/steadygate@v1` keeps
  working.
- **Fixed asset names:**
  - `steadygate-setup-windows.exe`
  - `steadygate-setup-macos.zip`
  - `steadygate-setup-linux`

  These names never change, so
  `https://github.com/Mesmoraz/steadygate/releases/latest/download/<name>` always points at
  the newest build.

## 6. Site changes (`site/index.html`, `site/style.css`, small JS)

### 6.1 Hero

- **Install now** becomes the primary button. A small script (`site/install.js`) picks the
  OS: it uses `navigator.userAgentData.platform` when available and falls back to the user
  agent. It sets the button's `href` and its label ("Install for Windows" and so on).
- **Without JavaScript, or when the OS is unknown,** the button links to `#install`.
- **Under the button:**
  - "Other platforms" links to the other two downloads and to `pip install`.
  - One line explains the unsigned-build warning on Windows ("More info → Run anyway") and
    on Mac (right-click → Open).
- **Secondary buttons:** *Try the demo* (to `#demo`) and *View on GitHub*.

### 6.2 New "Who it's for" section (directly after the hero, before the chatbot example)

Three cards. Each has a heading, "You're in the right place if…", and "What you get":
- **AI and LLM app teams.** You run evals with promptfoo, DeepEval or your own script, and a
  fixed threshold either blocks good releases or lets bad ones through. You get a go/no-go
  that knows your normal variation.
- **QA and release owners.** You sign off releases, and now you're asked to test AI features
  whose outputs vary. You get one clear verdict per run, an exit code your pipeline can act
  on, and a report in the pull request.
- **Teams with flaky tests.** Noisy end-to-end, performance or game tests cry wolf. You get
  repeated-trial pass rates with confidence intervals, so 7/10 isn't treated as a failure,
  and crashes are kept apart from real regressions.

Under the cards, one honest line on who it's **not** for: "If your tests give the same
answer every time, a plain pass/fail is all you need."

### 6.3 "Why you can trust the answer" strip (before the footer, after "Add it to your repo")

Five short points, aimed at reviewers and hiring managers:
- Errors can never make you pass.
- A fixed exit-code contract (0 / 1 / 2 / 3).
- Every answer on this page comes from the real engine running in your browser.
- More than 90% test coverage, enforced in CI on Windows, Mac and Linux.
- MIT licence.

### 6.4 `#install` section

The existing "Add it to your repo" section gets the id `install` and starts with the three
download links and `pip install`, followed by the existing workflow snippet.

### 6.5 Build

`SITE_FILES` in `site/build.py` gains `install.js`.

## 7. README

- Add a short **Who it's for** section after the intro table, with the same three audiences
  and the "not for" line.
- **Quick start** starts with "Download the installer" and the three links, then
  `steadygate init`, then keeps `pip install` as the alternative.
- Add a short **Uninstall** section (§4.5).

## 8. Testing

**Python:**
- `scaffold`:
  - The configs rendered for all four formats load through `load_config`.
  - The workflows rendered are valid YAML with the expected `results:`.
  - `plan_files` marks existing files.
  - `write_files` never overwrites a file that isn't in `overwrite`.
- `steadygate init`:
  - It runs non-interactively with flags.
  - It exits 3 when there's no TTY and no `--format`.
  - It leaves existing files alone without `--force`.
- `installer/state.py`:
  - page order
  - validation messages (missing folder, a tolerance outside (0, 1), an empty metric)
  - format changes refilling the defaults
- `installer/install.py`:
  - copying into a temporary directory
  - the POSIX profile line added once only
  - the Windows PATH logic against a fake registry, including "already present" and an
    empty `Path`

**Site:** `tests/test_site_build.py` checks that `install.js` is copied and that the three
download URLs in `index.html` use the fixed asset names.

**Release:** the per-OS smoke tests in §5. A `workflow_dispatch` run on the branch before
merging proves the builds work on all three operating systems.

**Manual (before the first tag):**
- Run the downloaded Windows build on this machine through all four pages.
- Open a new terminal and run `steadygate --version`.

**Coverage:** the existing `--cov=steadygate` gate (90% or above) covers `scaffold.py` and
`init`. `installer/` is outside the package, so it isn't part of that figure. Its logic
modules are tested directly, and the Tk widget code in `wizard.py` is only checked by hand.

## 9. Rollout

1. Merge this work. The site's buttons point at `releases/latest`, which will 404 until
   step 2.
2. The user approves, then a `v0.0.1` tag is pushed. The release workflow publishes the
   three files and creates `v1`.
3. Check that each download link resolves, and switch the site's workflow snippet to `@v1`.

To avoid a broken button between steps 1 and 2, `install.js` makes a `GET` request to
`api.github.com/repos/Mesmoraz/steadygate/releases/latest`. On a 404, it shows "Coming
soon: use `pip install`" in place of the button. On a network error or rate limit, it
leaves the button as it is.

## 10. Open risks

- **Unsigned builds.** SmartScreen and Gatekeeper friction may put off non-technical
  visitors. The site's warning line reduces this, and signing is a later step.
- **Size.** Each onefile Python build is roughly 10–15 MB, and the setup file is roughly
  double because it embeds the CLI. That's acceptable.
- **Linux display.** Tk needs a desktop session. When there's no display, the wizard prints
  a message pointing to `steadygate init`.
