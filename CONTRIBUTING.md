# Contributing to Steadygate

Thanks for helping. Bug reports, new adapters and clearer docs are all welcome.

## Before you start

- **Found a wrong verdict?** That's the most useful bug report there is. Open an issue with
  the results file (or a trimmed copy), your `steadygate.yml`, and the verdict you expected.
- **Want a new input format?** Open an issue with a small sample of the tool's output first,
  so we can agree how its errors and skips should map onto Steadygate's statuses.
- **Bigger changes** (a new rule, a config change): open an issue to talk it through before
  writing code.

## Setup

```bash
git clone https://github.com/Mesmoraz/steadygate
cd steadygate
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev,companion]"
```

## Checks

CI runs these on Linux, Windows and macOS. Run them before you push:

```bash
ruff format .
ruff check .
mypy
pytest            # fails under 90% coverage
```

## Ground rules

- **Errors never pass.** A crashed test, a judge timeout or a missing case must end up
  INCONCLUSIVE, never PASS. Every adapter needs a test that shows this.
- **The exit codes are the contract**: 0 PASS, 1 REGRESSION, 2 INCONCLUSIVE, 3 config error.
  Don't change what they mean.
- **No network calls and no telemetry** in the CLI.
- **Statistics need a source.** If you add or change a rule's math, cite where it comes from
  in the docstring and add a Hypothesis property test next to the example-based ones.
- Add a line under `[Unreleased]` in `CHANGELOG.md` for anything a user would notice.

## Adding an adapter

1. Add `src/steadygate/adapters/<name>.py` with a `load(path) -> list[Result]` function.
2. Register it, and its detection rule, in `src/steadygate/adapters/__init__.py`.
3. Add a small real-world fixture to `tests/fixtures/` and tests in `tests/test_adapters.py`
   covering pass, fail, skip and error.
4. Add a row to the "Input formats" table in the README.

## Releasing (maintainers)

1. Move the `[Unreleased]` notes in `CHANGELOG.md` under a new version heading.
2. Bump `version` in `pyproject.toml` and `__version__` in `src/steadygate/__init__.py`.
3. Merge to `main`, then push a tag `vX.Y.Z`. The release workflow checks that the tag
   matches the version, publishes to PyPI and creates the GitHub release with the installers.
