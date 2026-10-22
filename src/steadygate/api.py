"""The companion's HTTP API (version 1).

Routes only validate input and delegate to sessions, stats, baseline and the gate engine,
so the CLI and the companion always reach the same verdict on the same data.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException

from steadygate import baseline, gate, report
from steadygate import config as config_mod
from steadygate.collectors import CollectorError, describe_all, find_processes
from steadygate.config import Config
from steadygate.models import Verdict
from steadygate.sessions import Session, SessionError, SessionManager, SessionState
from steadygate.stats import summarize
from steadygate.store import Store

API_VERSION = 1
WEB_DIR = Path(__file__).parent / "web"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class CollectorSpec(BaseModel):
    id: str
    params: dict[str, Any] = Field(default_factory=dict)


class SessionIn(BaseModel):
    name: str = ""
    collectors: list[CollectorSpec] = Field(default_factory=list)
    interval_s: float = Field(1.0, gt=0, le=3600)


class MetricIn(BaseModel):
    metric: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    case: str | None = None


class ConfigIn(BaseModel):
    yaml: str


def _ok(**fields: Any) -> dict[str, Any]:
    return {"api": API_VERSION, **fields}


def _error(status: int, detail: Any) -> JSONResponse:
    return JSONResponse(_ok(detail=detail), status_code=status)


def _all_stats(session: Session) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for metric in session.metrics():
        values = session.ok_values(metric)
        if values:
            out[metric] = summarize(values).as_dict()
    return out


def _hostname(host_header: str) -> str:
    """``localhost:7777`` -> ``localhost``; ``[::1]:7777`` -> ``::1``."""
    if host_header.startswith("["):
        return host_header[1 : host_header.find("]")]
    return host_header.rsplit(":", 1)[0] if host_header.count(":") == 1 else host_header


def create_app(
    config_path: Path,
    manager: SessionManager | None = None,
    allow_origins: Sequence[str] = (),
    allowed_hosts: Sequence[str] = (),
) -> FastAPI:
    cfg = config_mod.load(config_path)
    if manager is None:
        manager = SessionManager(Store(cfg.store))
        manager.recover()
    mgr = manager

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        mgr.shutdown()

    app = FastAPI(title="Steadygate companion", version=str(API_VERSION), lifespan=lifespan)
    app.state.manager = mgr
    app.state.store = mgr.store
    app.state.config = cfg
    app.state.config_path = config_path
    allowed = set(allow_origins)
    hosts = LOOPBACK_HOSTS | set(allowed_hosts)

    @app.middleware("http")
    async def reject_foreign_origins(request: Request, call_next: Any) -> Response:
        # A web page on another site must not be able to drive the companion. Browsers
        # send Origin on cross-site requests; same-origin and non-browser clients pass.
        # Check Host first: with DNS rebinding, evil.example resolves to 127.0.0.1 and its
        # page's Origin then matches its own Host, so Host must be one of ours.
        host = request.headers.get("host", "")
        if _hostname(host) not in hosts:
            return _error(403, f"host {host!r} is not allowed")
        origin = request.headers.get("origin")
        own = f"{request.url.scheme}://{host}"
        if origin and origin != own and origin not in allowed:
            return _error(403, f"origin {origin} is not allowed; start with --allow-origin")
        response: Response = await call_next(request)
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(allowed),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(SessionError)
    async def _session_error(_: Request, exc: SessionError) -> JSONResponse:
        return _error(exc.status_code, str(exc))

    @app.exception_handler(CollectorError)
    async def _collector_error(_: Request, exc: CollectorError) -> JSONResponse:
        return _error(422, {"message": str(exc), "suggestions": exc.suggestions})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()]
        return _error(422, errors)

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return _error(exc.status_code, exc.detail)

    @app.get("/collectors")
    def list_collectors() -> dict[str, Any]:
        return _ok(collectors=[asdict(c) for c in describe_all()])

    @app.get("/processes")
    def list_processes(q: str = "") -> dict[str, Any]:
        return _ok(processes=find_processes(q))

    @app.post("/sessions")
    def start_session(body: SessionIn) -> dict[str, Any]:
        specs = [c.model_dump() for c in body.collectors]
        session = mgr.create(body.name, specs, body.interval_s)
        return _ok(**session.detail_dict())

    @app.get("/sessions")
    def list_sessions() -> dict[str, Any]:
        return _ok(sessions=[s.summary_dict() for s in mgr.list()])

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        return _ok(**mgr.get(session_id).detail_dict())

    @app.post("/sessions/{session_id}/metrics")
    def push_metrics(session_id: str, body: MetricIn | list[MetricIn]) -> dict[str, Any]:
        items = body if isinstance(body, list) else [body]
        accepted = mgr.push(session_id, [(m.metric, m.value, m.case) for m in items])
        return _ok(accepted=accepted)

    @app.post("/sessions/{session_id}/stop")
    def stop_session(session_id: str) -> dict[str, Any]:
        session = mgr.stop(session_id)
        return _ok(**session.summary_dict(), stats=_all_stats(session))

    @app.get("/sessions/{session_id}/stats")
    def session_stats(session_id: str, metric: str | None = None) -> dict[str, Any]:
        session = mgr.get(session_id)
        if metric is None:
            return _ok(stats=_all_stats(session))
        values = session.ok_values(metric)
        if not values:
            raise SessionError(f"no usable values for metric {metric!r}", 404)
        return _ok(metric=metric, **summarize(values).as_dict())

    @app.post("/sessions/{session_id}/record")
    def record_session(session_id: str) -> dict[str, Any]:
        session = mgr.get(session_id)
        if session.state is SessionState.RUNNING:
            raise SessionError(f"session {session_id} is still running; stop it first", 409)
        n = mgr.store.record(session.id, session.snapshot())
        return _ok(recorded=n, runs=mgr.store.run_count())

    @app.post("/sessions/{session_id}/gate")
    def gate_session(session_id: str, record: bool = False) -> dict[str, Any]:
        session = mgr.get(session_id)
        if session.state is SessionState.RUNNING:
            session = mgr.stop(session_id)
        results = session.snapshot()
        cfg: Config = app.state.config
        outcome = gate.evaluate(cfg, results, mgr.store, run_id=session.id)
        recorded = False
        if record and outcome.verdict is Verdict.PASS:
            mgr.store.record(session.id, results)
            recorded = True
        return _ok(
            **report.as_dict(outcome),
            code=int(outcome.verdict),
            interrupted=session.state is SessionState.INTERRUPTED,
            recorded=recorded,
        )

    @app.get("/baseline/suggest")
    def suggest_baseline(metric: str, direction: str | None = None) -> dict[str, Any]:
        cfg: Config = app.state.config
        history = mgr.store.run_aggregates(metric, cfg.window)
        try:
            suggestion = baseline.suggest(
                metric, history, direction or _direction_hint(metric), cfg.min_history
            )
        except ValueError as exc:
            return _error(422, str(exc))  # type: ignore[return-value]
        return _ok(**suggestion.as_dict())

    @app.get("/config")
    def get_config() -> dict[str, Any]:
        return _ok(yaml=config_path.read_text(encoding="utf-8"))

    @app.put("/config")
    def put_config(body: ConfigIn) -> Any:
        try:
            raw = yaml.safe_load(body.yaml)
        except yaml.YAMLError as exc:
            return _error(422, f"not valid YAML: {exc}")
        try:
            new_cfg = config_mod.parse(raw, config_path.parent)
        except config_mod.ConfigError as exc:
            return _error(422, str(exc))
        # Write beside the file, then swap, so a crash never leaves a half-written config.
        tmp = config_path.with_suffix(".yml.tmp")
        tmp.write_text(body.yaml, encoding="utf-8", newline="")
        os.replace(tmp, config_path)
        app.state.config = new_cfg
        return _ok(yaml=body.yaml, rules=len(new_cfg.rules))

    # Mounted last so every API route above takes precedence over a static file.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="dashboard")
    return app


def _direction_hint(metric: str) -> str:
    for info in describe_all():
        if info.metrics == "any":
            continue
        for m in info.metrics:
            if m.name == metric and m.better == "lower":
                return "lower_is_better"
    return "higher_is_better"
