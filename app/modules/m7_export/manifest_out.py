"""运行清单导出：把 run_id、输入 sha256、参数、水位与计数打成一份可复核文件。"""
from __future__ import annotations

import json

from ... import config, manifest as manifest_mod


def build(store, batch_id: str) -> dict:
    doc = store.load_manifest(batch_id)
    batch = store.get_batch(batch_id) or {}
    if doc is None:
        doc = _fallback(store, batch)
    return {
        "run_id": doc.get("run_id") or batch.get("run_id"),
        "batch_id": batch_id,
        "batch_key": batch.get("batch_key"),
        "rule_version": config.RULE_VERSION,
        "timezone": "入库 UTC，页面按 Asia/Shanghai(+08:00)",
        "money": "金额 Decimal，单位元，最小刻度 0.01",
        "inputs": doc.get("inputs", []),
        "input_digest": doc.get("input_digest") or batch.get("input_digest"),
        "params": doc.get("params", {}),
        "started_at": doc.get("started_at"),
        "finished_at": doc.get("finished_at"),
        "elapsed_ms": doc.get("elapsed_ms", 0),
        "watermark_utc": doc.get("watermark_utc") or batch.get("watermark_utc"),
        "max_event_time_utc": doc.get("max_event_time_utc")
        or batch.get("max_event_time"),
        "stages": doc.get("stages", _stages(store, batch_id)),
        "counters": doc.get("counters", store.load_totals(batch_id)),
        "reconcile": doc.get("reconcile", []),
        "replayable": "同一 input_digest 重跑命中 batch_key，不新增行",
    }


def _fallback(store, batch: dict) -> dict:
    return {
        "run_id": batch.get("run_id"),
        "batch_id": batch.get("batch_id"),
        "input_digest": batch.get("input_digest"),
        "inputs": store.load_inputs(batch.get("batch_id") or ""),
        "stages": _stages(store, batch.get("batch_id") or ""),
        "watermark_utc": batch.get("watermark_utc"),
        "max_event_time_utc": batch.get("max_event_time"),
        "counters": store.load_totals(batch.get("batch_id") or ""),
    }


def _stages(store, batch_id: str) -> list[dict]:
    return [{"stage": s["stage"], "in": s["in_count"], "out": s["out_count"],
             "quarantine": s["quarantine_count"], "status": s["status"]}
            for s in store.load_stages(batch_id)]


def document(batch_id: str) -> str:
    payload = {"batch_id": batch_id}
    return json.dumps(payload, ensure_ascii=False, indent=2)


def digest_of(payload: dict) -> str:
    return manifest_mod.digest_parts([json.dumps(payload, ensure_ascii=False,
                                                 sort_keys=True)])
