from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient

from steadygate.api import create_app


def _start(client: TestClient, **body: object) -> str:
    r = client.post("/sessions", json={"collectors": [], **body})
    assert r.status_code == 200, r.text
    return str(r.json()["id"])


def test_collectors_lists_metrics(client: TestClient) -> None:
    r = client.get("/collectors")
    assert r.status_code == 200
    body = r.json()
    assert body["api"] == 1
    process = next(c for c in body["collectors"] if c["id"] == "process")
    rss = next(m for m in process["metrics"] if m["name"] == "rss_mb")
    assert rss["better"] == "lower"


def test_processes_search(client: TestClient) -> None:
    pids = [p["pid"] for p in client.get("/processes", params={"q": "python"}).json()["processes"]]
    assert os.getpid() in pids


def test_push_only_flow(client: TestClient) -> None:
    sid = _start(client)
    r = client.post(
        f"/sessions/{sid}/metrics", json=[{"metric": "m", "value": 1}, {"metric": "m", "value": 3}]
    )
    assert r.json() == {"api": 1, "accepted": 2}
    r = client.post(f"/sessions/{sid}/metrics", json={"metric": "m", "value": 2, "case": "lvl1"})
    assert r.json()["accepted"] == 1
    stopped = client.post(f"/sessions/{sid}/stop").json()
    assert stopped["state"] == "stopped"
    assert stopped["stats"]["m"]["n"] == 3
    stats = client.get(f"/sessions/{sid}/stats", params={"metric": "m"}).json()
    assert (stats["median"], stats["n"], stats["metric"]) == (2.0, 3, "m")


def test_stats_all_metrics(client: TestClient) -> None:
    sid = _start(client)
    client.post(
        f"/sessions/{sid}/metrics", json=[{"metric": "a", "value": 1}, {"metric": "b", "value": 2}]
    )
    assert set(client.get(f"/sessions/{sid}/stats").json()["stats"]) == {"a", "b"}


def test_stats_unknown_metric_404(client: TestClient) -> None:
    sid = _start(client)
    r = client.get(f"/sessions/{sid}/stats", params={"metric": "nope"})
    assert r.status_code == 404
    assert r.json()["api"] == 1


def test_list_and_get_sessions(client: TestClient) -> None:
    sid = _start(client, name="demo")
    client.post(f"/sessions/{sid}/metrics", json={"metric": "m", "value": 1})
    assert [s["id"] for s in client.get("/sessions").json()["sessions"]] == [sid]
    detail = client.get(f"/sessions/{sid}").json()
    assert detail["name"] == "demo"
    assert detail["results"][0]["value"] == 1.0


def test_process_session_samples(client: TestClient, busy_proc: subprocess.Popen[bytes]) -> None:
    sid = _start(
        client, collectors=[{"id": "process", "params": {"pid": busy_proc.pid}}], interval_s=0.2
    )
    time.sleep(0.7)
    client.post(f"/sessions/{sid}/stop")
    assert client.get(f"/sessions/{sid}/stats", params={"metric": "rss_mb"}).json()["n"] >= 2


def test_unknown_process_422_with_suggestions(client: TestClient) -> None:
    r = client.post(
        "/sessions", json={"collectors": [{"id": "process", "params": {"name": "pythn"}}]}
    )
    assert r.status_code == 422
    assert isinstance(r.json()["detail"]["suggestions"], list)


def test_nan_rejected_422(client: TestClient) -> None:
    sid = _start(client)
    for raw in ('{"metric":"m","value":NaN}', '{"metric":"m","value":1e999}'):
        r = client.post(
            f"/sessions/{sid}/metrics", content=raw, headers={"content-type": "application/json"}
        )
        assert r.status_code == 422, raw
        assert r.json()["api"] == 1
    assert client.get(f"/sessions/{sid}").json()["results"] == []


def test_bad_interval_422(client: TestClient) -> None:
    assert client.post("/sessions", json={"interval_s": 0}).status_code == 422


def test_push_to_stopped_409(client: TestClient) -> None:
    sid = _start(client)
    client.post(f"/sessions/{sid}/stop")
    r = client.post(f"/sessions/{sid}/metrics", json={"metric": "m", "value": 1})
    assert r.status_code == 409


def test_unknown_session_404(client: TestClient) -> None:
    assert client.get("/sessions/s_00000000").status_code == 404


def test_stop_twice_ok(client: TestClient) -> None:
    sid = _start(client)
    first = client.post(f"/sessions/{sid}/stop").json()
    second = client.post(f"/sessions/{sid}/stop")
    assert second.status_code == 200
    assert second.json()["stopped_at"] == first["stopped_at"]


def test_cors_only_allowed_origin(companion_config: Path) -> None:
    app = create_app(companion_config, allow_origins=["http://localhost:3000"])
    with TestClient(app) as c:
        evil = c.post("/sessions", json={}, headers={"Origin": "http://evil"})
        assert evil.status_code == 403
        assert "access-control-allow-origin" not in evil.headers
        ok = c.post("/sessions", json={}, headers={"Origin": "http://localhost:3000"})
        assert ok.status_code == 200
        assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
        own = c.post("/sessions", json={}, headers={"Origin": "http://testserver"})
        assert own.status_code == 200


def test_dashboard_served(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "<title>Steadygate</title>" in page.text
    assert '<script src="app.js"' in page.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200
    assert client.get("/collectors").json()["api"] == 1  # API routes still win
