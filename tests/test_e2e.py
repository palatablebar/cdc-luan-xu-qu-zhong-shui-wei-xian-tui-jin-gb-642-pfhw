"""端到端：样例 → 采信 → 归一 → 去重冲突 → 水位 → 快照 → 导出 → 页面。"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import create_app
from fixtures import expected as X


def test_full_pipeline_matches_expected(run):
    assert run["status"] == "done"
    assert run["counters"] == {"applied": X.APPLIED, "duplicates": X.DUPLICATES,
                              "conflicts": X.CONFLICTS, "quarantine": X.QUARANTINE,
                              "total": X.RAW_LINES}
    totals = run["totals"]
    assert totals["raw_lines"] == X.RAW_LINES
    assert totals["blank_lines"] == X.BLANK_LINES
    assert totals["late_events"] == X.LATE_EVENTS
    assert totals["watermark_utc"] == X.WATERMARK
    assert totals["snapshot_keys"] == X.SNAPSHOT_KEYS
    assert totals["amount_snapshot"] == X.AMOUNT_SNAPSHOT
    assert totals["amount_applied"] == X.AMOUNT_APPLIED
    assert run["all_balanced"] is True
    assert [row["name"] for row in run["reconciliation"]] == ["行账", "键账", "快照账", "金额账"]
    assert [stage["stage"] for stage in run["stages"]] == [
        "ingest", "contract", "dedup", "watermark", "snapshot", "export"]


def test_all_pages_return_200(client):
    pages = {
        "/": "水位时间轴",
        "/events": "事件流",
        "/schema": "契约",
        "/conflicts": "冲突与隔离",
        "/snapshot": "快照",
        "/batches": "批次",
    }
    for path, marker in pages.items():
        response = client.get(path)
        assert response.status_code == 200, path
        assert marker in response.text, path
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["watermark_utc"] == X.WATERMARK
    assert health["applied"] == X.APPLIED


def test_api_surface(client):
    events = client.get("/api/events").json()
    assert events["count"] == X.RAW_LINES
    conflicts = client.get("/api/conflicts").json()
    assert conflicts["counters"]["conflicts"] == X.CONFLICTS
    assert len(conflicts["quarantine"]) == X.QUARANTINE
    watermark = client.get("/api/watermark").json()
    assert watermark["watermark"]["late"] == X.LATE_EVENTS
    assert len(watermark["ticks"]["ticks"]) == X.APPLIED
    snapshot = client.get("/api/snapshot").json()
    assert len(snapshot["rows"]) == X.SNAPSHOT_KEYS
    schema = client.get("/api/schema").json()
    assert schema["late_window_minutes"] == 10
    batches = client.get("/api/batches").json()
    assert batches["count"] == 1


def test_failures_and_idempotent_import(client):
    bad = client.post("/api/events/import", json={"source": "nope"})
    assert bad.status_code == 400
    assert bad.json()["error"] == "E_BAD_SOURCE"

    missing = client.get("/api/batches/B-0000000000/manifest")
    assert missing.status_code == 404

    first = client.post("/api/events/import", json={"source": "all"}).json()
    assert first["idempotent_replay"] is True
    assert first["batch_id"] == client.get("/api/health").json()["batch_id"]

    for kind in ("events", "quarantine", "conflicts", "snapshot"):
        response = client.get(f"/api/export/{kind}.csv")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert len(response.content) > 100
    manifest = client.get("/api/export/manifest.json")
    assert manifest.status_code == 200
    payload = json.loads(manifest.content.decode("utf-8"))
    assert payload["watermark_utc"] == X.WATERMARK

    materialized = client.post("/api/snapshot", json={}).json()
    assert materialized["rows"] == X.SNAPSHOT_KEYS
    assert all(row["ok"] for row in materialized["reconcile"])


def test_empty_state_pages_render(settings):
    """空态：库里还没有批次时 6 个页面都要 200，不能 500。"""
    app = create_app(settings, seed=False)
    with TestClient(app) as client:
        for path in ("/", "/events", "/schema", "/conflicts", "/snapshot", "/batches"):
            response = client.get(path)
            assert response.status_code == 200, path
        assert client.get("/api/health").json()["batch_id"] is None
        snapshot = client.get("/snapshot")
        assert "还没有快照。" in snapshot.text
        assert "0.00" in snapshot.text
