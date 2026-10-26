from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from steadygate.api import create_app
from steadygate.store import Store


def _session(client: TestClient, values: list[float], metric: str = "m", stop: bool = True) -> str:
    sid = str(client.post("/sessions", json={}).json()["id"])
    client.post(f"/sessions/{sid}/metrics", json=[{"metric": metric, "value": v} for v in values])
    if stop:
        client.post(f"/sessions/{sid}/stop")
    return sid


def _record_baselines(client: TestClient) -> None:
    for v in (100.0, 101.0, 99.0):
        r = client.post(f"/sessions/{_session(client, [v])}/record")
        assert r.status_code == 200, r.text


def test_full_flow_pass_then_regression(client: TestClient) -> None:
    _record_baselines(client)
    suggestion = client.get("/baseline/suggest", params={"metric": "m"}).json()
    assert suggestion["api"] == 1
    assert suggestion["history_runs"] == 3
    assert suggestion["suggested"]["max_regression"] == 0.05
    assert suggestion["suggested"]["direction"] == "higher_is_better"

    good = client.post(f"/sessions/{_session(client, [100.0])}/gate").json()
    assert (good["verdict"], good["code"]) == ("pass", 0)
    bad = client.post(f"/sessions/{_session(client, [80.0])}/gate").json()
    assert (bad["verdict"], bad["code"]) == ("regression", 1)
    assert bad["rules"][0]["rule"] == "coverage"  # inferred from the baseline
    assert bad["rules"][1]["outcome"] == "fail"


def test_record_returns_counts(client: TestClient) -> None:
    r = client.post(f"/sessions/{_session(client, [1.0, 2.0])}/record").json()
    assert (r["recorded"], r["runs"]) == (2, 1)


def test_no_baseline_before_history(client: TestClient) -> None:
    r = client.post(f"/sessions/{_session(client, [1.0])}/gate").json()
    assert r["code"] == 0
    assert r["rules"][0]["outcome"] == "no_baseline"


def test_gate_with_no_values_is_inconclusive(client: TestClient) -> None:
    _record_baselines(client)
    r = client.post(f"/sessions/{_session(client, [5.0], metric='other')}/gate").json()
    assert r["code"] == 2


def test_gate_stops_running_session(client: TestClient) -> None:
    sid = _session(client, [1.0], stop=False)
    client.post(f"/sessions/{sid}/gate")
    assert client.get(f"/sessions/{sid}").json()["state"] == "stopped"


def test_gate_record_only_on_pass(client: TestClient) -> None:
    _record_baselines(client)
    bad = client.post(f"/sessions/{_session(client, [80.0])}/gate", params={"record": "true"})
    assert bad.json()["recorded"] is False
    good = client.post(f"/sessions/{_session(client, [100.0])}/gate", params={"record": "true"})
    assert good.json()["recorded"] is True
    assert client.app.state.store.run_count() == 4  # type: ignore[attr-defined]


def test_interrupted_session_gate_flag(companion_config: Path) -> None:
    with Store(companion_config.parent / "history.db") as store:
        store.save_session("s_0000abcd", "", "running", "2026-10-21T00:00:00+00:00", None, {})
    with TestClient(create_app(companion_config), base_url="http://127.0.0.1") as c:
        r = c.post("/sessions/s_0000abcd/gate").json()
        assert r["interrupted"] is True
        assert c.get("/sessions/s_0000abcd").json()["state"] == "interrupted"


def test_record_running_409(client: TestClient) -> None:
    sid = _session(client, [1.0], stop=False)
    assert client.post(f"/sessions/{sid}/record").status_code == 409


def test_suggest_uses_collector_hint(client: TestClient) -> None:
    r = client.get("/baseline/suggest", params={"metric": "rss_mb"}).json()
    assert r["suggested"] is None
    assert r["rationale"] == "0/3 runs recorded"
    store: Store = client.app.state.store  # type: ignore[attr-defined]
    from steadygate.models import Result

    for i, v in enumerate((400.0, 404.0, 396.0)):
        store.record(f"r{i}", [Result("t=0", "rss_mb", v)])
    r = client.get("/baseline/suggest", params={"metric": "rss_mb"}).json()
    assert r["suggested"]["direction"] == "lower_is_better"
    explicit = client.get(
        "/baseline/suggest", params={"metric": "rss_mb", "direction": "higher_is_better"}
    ).json()
    assert explicit["suggested"]["direction"] == "higher_is_better"


def test_suggest_bad_direction_422(client: TestClient) -> None:
    r = client.get("/baseline/suggest", params={"metric": "m", "direction": "up"})
    assert r.status_code == 422


VALID = """\
store: history.db
rules:
  - type: relative_delta
    metric: m
    max_regression: 0.10
  - type: absolute
    metric: m
    min: 0
"""


def test_config_roundtrip(client: TestClient, companion_config: Path) -> None:
    r = client.put("/config", json={"yaml": VALID})
    assert r.status_code == 200, r.text
    assert r.json()["rules"] == 2
    assert client.get("/config").json()["yaml"] == VALID
    assert companion_config.read_text(encoding="utf-8") == VALID
    assert len(client.app.state.config.rules) == 2  # type: ignore[attr-defined]


def test_config_invalid_keeps_file(client: TestClient, companion_config: Path) -> None:
    before = companion_config.read_bytes()
    rules_before = client.app.state.config.rules  # type: ignore[attr-defined]
    for bad in ("rules: [", "rules:\n  - type: nope\n"):
        r = client.put("/config", json={"yaml": bad})
        assert r.status_code == 422
        assert r.json()["api"] == 1
    assert companion_config.read_bytes() == before
    assert client.app.state.config.rules == rules_before  # type: ignore[attr-defined]
