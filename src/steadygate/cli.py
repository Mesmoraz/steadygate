"""Command-line entry point.

Exit codes: 0 PASS · 1 REGRESSION · 2 INCONCLUSIVE · 3 config/usage error
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from steadygate import __version__, config, gate, report, scaffold
from steadygate.adapters import ADAPTERS, AdapterError, load_results
from steadygate.models import Verdict
from steadygate.store import Store


def _default_run_id() -> str:
    return os.environ.get("GITHUB_SHA") or datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "results",
        type=Path,
        help="results file (JUnit XML, JSONL, or pytest-json-report, promptfoo or Playwright JSON)",
    )
    p.add_argument("--format", choices=sorted(ADAPTERS), help="override format detection")
    p.add_argument(
        "-c", "--config", type=Path, default=Path(config.DEFAULT_CONFIG), help="config file"
    )
    p.add_argument("--run-id", help="identifier for this run (default: $GITHUB_SHA or timestamp)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steadygate",
        description="A statistical release gate for non-deterministic systems.",
    )
    parser.add_argument("--version", action="version", version=f"steadygate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="gate a results file against the baseline")
    _add_common(run)
    run.add_argument(
        "--record", action="store_true", help="add this run to the baseline if it passes"
    )
    run.add_argument(
        "--require-history",
        action="store_true",
        help="INCONCLUSIVE instead of PASS when the baseline has no runs (e.g. on pull requests)",
    )
    run.add_argument("--markdown", type=Path, help="also write a markdown report (for PR comments)")
    run.add_argument("--json", action="store_true", help="print a JSON report instead of text")

    rec = sub.add_parser("record", help="add a results file to the baseline without gating")
    _add_common(rec)
    rec.add_argument("--ref", default="", help="git ref or label to store with the run")

    srv = sub.add_parser("serve", help="run the companion: local HTTP API and dashboard")
    srv.add_argument("--host", default="127.0.0.1", help="interface to listen on")
    srv.add_argument("--port", type=int, default=7777, help="port to listen on")
    srv.add_argument(
        "-c", "--config", type=Path, default=Path(config.DEFAULT_CONFIG), help="config file"
    )
    srv.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        metavar="URL",
        help="let a web page at this origin call the API (repeatable)",
    )

    ini = sub.add_parser("init", help="write a starter steadygate.yml and GitHub workflow")
    ini.add_argument("--dir", type=Path, default=Path("."), help="project folder (default: .)")
    ini.add_argument("--format", choices=scaffold.FORMATS, help="format of your results file")
    ini.add_argument("--results", help="path to your results file")
    ini.add_argument("--metric", help="metric to gate on")
    ini.add_argument("--tolerance", type=float, help="allowed drop, e.g. 0.05 for 5%%")
    ini.add_argument("--eval-command", help="command that runs your evals in CI")
    ini.add_argument("--force", action="store_true", help="overwrite files that already exist")
    return parser


def _config_error(message: str) -> int:
    print(f"steadygate: error: {message}", file=sys.stderr)
    return int(Verdict.CONFIG_ERROR)


def _port_free(host: str, port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from steadygate.api import create_app
    except ImportError:
        return _config_error(
            'the companion needs extra packages: pip install "steadygate[companion]"'
        )
    if not _port_free(args.host, args.port):
        return _config_error(f"port {args.port} is in use; try --port")
    try:
        app = create_app(args.config, allow_origins=args.allow_origin, allowed_hosts=[args.host])
    except config.ConfigError as exc:
        return _config_error(str(exc))
    print(f"steadygate companion on http://{args.host}:{args.port}  (API docs: /docs)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


_TOLERANCE_MSG = "tolerance must be between 0 and 1, e.g. 0.05 for 5%"


def _ask(label: str, default: str) -> str:
    shown = f" [{default}]" if default else ""
    return input(f"{label}{shown}: ").strip() or default


def _init(args: argparse.Namespace) -> int:
    fmt = args.format
    interactive = fmt is None and sys.stdin.isatty()
    if fmt is None:
        if not interactive:
            return _config_error("--format is required when not run in a terminal")
        fmt = _ask(f"Results format ({', '.join(scaffold.FORMATS)})", "")
        if fmt not in scaffold.FORMATS:
            return _config_error(f"format must be one of: {', '.join(scaffold.FORMATS)}")
    base = scaffold.default_answers(fmt)
    results = args.results or base.results_path
    metric = args.metric or base.metric
    tolerance = base.tolerance if args.tolerance is None else args.tolerance
    command = base.eval_command if args.eval_command is None else args.eval_command
    if interactive:
        results = _ask("Results file", results)
        metric = _ask("Metric", metric)
        raw = _ask("Tolerance (0.05 = 5%)", f"{tolerance:g}")
        try:
            tolerance = float(raw)
        except ValueError:
            return _config_error(_TOLERANCE_MSG)
        command = _ask("Command that runs your evals (optional)", command)
    if not 0 < tolerance < 1:
        return _config_error(_TOLERANCE_MSG)
    answers = scaffold.Answers(fmt, results, metric, tolerance, command)
    planned = scaffold.plan_files(args.dir, answers)
    overwrite = {f.path for f in planned if f.exists} if args.force else set()
    written = set(scaffold.write_files(planned, overwrite))
    for item in planned:
        if item.path in written:
            print(f"wrote {item.path}")
        else:
            print(f"kept existing {item.path} (use --force to replace)")
    print()
    print("Next steps:")
    print(f"  git add {scaffold.CONFIG_PATH} {scaffold.WORKFLOW_PATH.as_posix()}")
    print("  git commit, then push to main to start building history")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    if args.command == "init":
        return _init(args)
    run_id: str = args.run_id or _default_run_id()

    try:
        cfg = config.load(args.config)
        results = load_results(args.results, args.format)
    except (config.ConfigError, AdapterError) as exc:
        return _config_error(str(exc))

    with Store(cfg.store) as store:
        if args.command == "record":
            n = store.record(run_id, results, ref=args.ref)
            print(f"steadygate: recorded {n} result(s) as run {run_id} ({store.run_count()} runs)")
            return 0

        outcome = gate.evaluate(
            cfg, results, store, run_id=run_id, require_history=args.require_history
        )
        print(report.as_json(outcome) if args.json else report.text(outcome))
        if args.markdown:
            args.markdown.write_text(report.markdown(outcome), encoding="utf-8")
        if args.record and outcome.verdict is Verdict.PASS:
            n = store.record(run_id, results)
            print(f"steadygate: recorded {n} result(s) as run {run_id}", file=sys.stderr)
        return int(outcome.verdict)
