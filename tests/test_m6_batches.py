"""M6 批次：批次键、幂等重放、陈旧拒绝、续跑计划、运行清单。"""
from __future__ import annotations

import pathlib
from dataclasses import replace

import pytest

from app import config, errors
from app.modules.m6_batches import history, resume, service, state
from fixtures import expected as X

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "sample_data"


def test_same_input_replays_same_batch(settings, store):
    first = service.run(settings=settings, store=store, source="all")
    second = service.run(settings=settings, store=store, source="all")
    assert second["idempotent_replay"] is True
    assert second["replay_of"] == first["batch"]["batch_id"]
    assert second["batch"]["batch_id"] == first["batch"]["batch_id"]
    assert len(store.list_batches()) == 1


def test_replay_adds_no_rows(settings, store):
    first = service.run(settings=settings, store=store, source="all")
    batch_id = first["batch"]["batch_id"]
    before = store.count_rows(batch_id)
    service.run(settings=settings, store=store, source="all")
    after = store.count_rows(batch_id)
    assert before == after
    assert after["events"] == X.RAW_LINES
    assert after["snapshots"] == X.SNAPSHOT_KEYS


def test_stale_input_rejected(settings, store, tmp_path):
    """TRAP-26：输入最大 event_time 落后于已持久化水位 → 409，不覆盖。"""
    service.run(settings=settings, store=store, source="all")
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "events_old.jsonl").write_text(
        "# cdc-export v2 rows=1 charset=utf-8\n"
        '{"table":"orders","pk":"7001","version":9,"op":"upsert",'
        '"event_time":"2026-11-15T06:00:00Z","schema":"v2",'
        '"payload":{"status":"新建","amount":"1.00"}}\n',
        encoding="utf-8")
    stale_settings = replace(settings, sample_dir=str(old_dir))
    with pytest.raises(errors.StaleBatch) as info:
        service.run(settings=stale_settings, store=store, source="events_old.jsonl")
    assert info.value.status_code == 409
    assert info.value.code == "C_STALE_BATCH"
    assert len(store.list_batches()) == 1
    assert store.watermark_high() == X.WATERMARK


def test_batch_key_includes_params():
    digest = "a" * 64
    base = state.batch_key(digest, "cdcwater/1.0.0", {})
    params = state.batch_key(digest, "cdcwater/1.0.0", {"window": 10})
    version = state.batch_key(digest, "cdcwater/9.9.9", {})
    assert len({base, params, version}) == 3
    assert base == state.batch_key(digest, "cdcwater/1.0.0", {"empty": ""})


def test_resume_plan_for_done_batch(settings, store):
    run = service.run(settings=settings, store=store, source="all")
    plan = resume.plan(store, run["batch"]["batch_id"])
    assert plan["can_resume"] is False
    assert plan["code"] == "B_IDEMPOTENT_REPLAY"
    assert plan["is_latest"] is True


def test_resume_plan_for_failed_batch(store):
    store.save_attempt(batch={
        "batch_id": "B-failed01", "batch_key": "failed01", "run_id": "CDC-x",
        "source": "all", "status": "failed", "created_at": "2026-11-15T09:00:00Z",
        "input_digest": "digest", "params": {},
    }, inputs=[])
    plan = resume.plan(store, "B-failed01")
    assert plan["can_resume"] is True
    assert plan["from_stage"] == "ingest"
    assert resume.resume_note(plan) == "从 ingest 续跑"


def test_manifest_has_run_id_and_input_sha(settings, store):
    run = service.run(settings=settings, store=store, source="all")
    doc = store.load_manifest(run["batch"]["batch_id"])
    assert doc["run_id"].startswith("CDC-")
    assert doc["input_digest"] == run["batch"]["input_digest"]
    assert len(doc["inputs"]) == X.FILES
    names = [item["name"] for item in doc["inputs"]]
    assert names == list(config.SOURCE_ORDER)
    for item in doc["inputs"]:
        assert len(item["sha256"]) == 64
        assert item["bytes"] > 0
    assert doc["watermark_utc"] == X.WATERMARK
    assert doc["late_window_minutes"] == 10
    assert [stage["stage"] for stage in doc["stages"]][0] == "ingest"


def test_history_rows_show_counters(settings, store):
    service.run(settings=settings, store=store, source="all")
    rows = history.rows(store)
    assert len(rows) == 1
    row = rows[0]
    assert row["counters"]["applied"] == X.APPLIED
    assert row["counters"]["conflicts"] == X.CONFLICTS
    assert row["reconcile_ok"] is True
    assert row["watermark_utc"] == X.WATERMARK
    detail = history.detail(store, row["batch_id"])
    assert detail["input_digest"] == row["input_digest"]
    assert len(detail["reconcile"]) == 4


def test_watermark_note_regress():
    note = state.watermark_note("2026-11-15T08:52:00Z", "2026-11-15T07:00:00Z")
    assert note["state"] == "regress"
    advance = state.watermark_note("2026-11-15T08:52:00Z", "2026-11-15T09:00:00Z")
    assert advance["state"] == "advance"
    first = state.watermark_note(None, "2026-11-15T09:00:00Z")
    assert first["state"] == "first"
    assert state.is_stale("2026-11-15T07:00:00Z", "2026-11-15T08:52:00Z") is True
    assert state.is_stale("2026-11-15T09:00:00Z", "2026-11-15T08:52:00Z") is False


def test_clean_sample_uses_same_path(settings, store):
    """干净样例与脏样例走同一条代码路径：无脏行时计数全为 0。"""
    run = service.run(settings=settings, store=store, source=X.CLEAN_SOURCE)
    assert run["counters"]["applied"] == X.CLEAN_APPLIED
    assert run["counters"]["duplicates"] == 0
    assert run["counters"]["conflicts"] == 0
    assert run["counters"]["quarantine"] == 0
    assert run["totals"]["late_events"] == X.CLEAN_LATE
    assert run["totals"]["snapshot_as_of"] == X.CLEAN_AS_OF
    assert run["totals"]["snapshot_keys"] == X.CLEAN_SNAPSHOT_KEYS


def test_source_selection_and_unknown_source(settings, store):
    main_run = service.run(settings=settings, store=store, source="main")
    assert main_run["batch"]["source"] == "main"
    assert main_run["counters"]["total"] == X.MAIN_ACTUAL_ROWS
    assert main_run["totals"]["max_event_time"] == X.MAX_EVENT_TIME
    with pytest.raises(errors.BadRequest) as info:
        service.run(settings=settings, store=store, source="nope")
    assert info.value.code == "E_BAD_SOURCE"
