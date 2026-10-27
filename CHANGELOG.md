# Changelog

All notable changes to Steadygate are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Before 1.0, a minor version may change the config
format or the CLI; the exit-code contract (0 PASS, 1 REGRESSION, 2 INCONCLUSIVE, 3 config
error) will not change.

## [Unreleased]

## [0.1.0] - 2026-10-27

The first release on PyPI.

### Added

- `steadygate run` and `steadygate record`: gate a results file against recorded history,
  with exit codes 0 PASS, 1 REGRESSION, 2 INCONCLUSIVE and 3 config error.
- Rules: `absolute`, `relative_delta` (against the median of recent runs), `mad_outlier` and
  `pass_rate` (Wilson score interval over repeated trials). Each can be required or optional.
- Missing cases are INCONCLUSIVE, never a silent pass: from an `expected_cases` list, or
  inferred from every case the recent baseline runs always reported.
- Adapters for JSONL, JUnit XML, pytest-json-report, promptfoo and the Playwright JSON
  reporter. Crashes, timeouts and provider errors are kept apart from real failures.
- `steadygate flaky`: per-case pass rate with a confidence interval, flip rate, and a list
  of quarantine candidates, across one or more results files or the recorded history.
- `steadygate suggest`: `relative_delta` rules sized from your own run-to-run noise.
- `steadygate init`: writes a starter `steadygate.yml` and GitHub workflow.
- `steadygate serve` (with the `companion` extra): a local HTTP API and dashboard that samples
  a running process or takes metrics pushed from any language.
- A GitHub Action that records history on the default branch, gates pull requests, and
  posts one updating PR comment. A pull request with no history is INCONCLUSIVE unless
  `missing-history: pass` is set.
- Setup wizards for Windows, macOS and Linux, and a live in-browser demo.

[Unreleased]: https://github.com/Mesmoraz/steadygate/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Mesmoraz/steadygate/releases/tag/v0.1.0
