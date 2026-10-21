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

from steadygate import __version__, config, gate, report
from steadygate.adapters import ADAPTERS, AdapterError, load_results
from steadygate.models import Verdict
from steadygate.store import Store


def _default_run_id() -> str:
    return os.environ.get("GITHUB_SHA") or datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("results", type=Path, help="results file (JUnit XML or JSONL)")
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
        app = create_app(args.config, allow_origins=args.allow_origin)
    except config.ConfigError as exc:
        return _config_error(str(exc))
    print(f"steadygate companion on http://{args.host}:{args.port}  (API docs: /docs)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args)
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

        outcome = gate.evaluate(cfg, results, store, run_id=run_id)
        print(report.as_json(outcome) if args.json else report.text(outcome))
        if args.markdown:
            args.markdown.write_text(report.markdown(outcome), encoding="utf-8")
        if args.record and outcome.verdict is Verdict.PASS:
            n = store.record(run_id, results)
            print(f"steadygate: recorded {n} result(s) as run {run_id}", file=sys.stderr)
        return int(outcome.verdict)
