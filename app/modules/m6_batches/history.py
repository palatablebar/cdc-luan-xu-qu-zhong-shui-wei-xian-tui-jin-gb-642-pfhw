"""批次历史：一次运行的输入摘要、水位、计数与对账状态，供批次页与导出使用。"""
from __future__ import annotations

import json

from ..m2_schema.coerce import iso_utc, parse_iso


def rows(store, limit: int = 30) -> list[dict]:
    out = []
    for batch in store.list_batches(limit=limit):
        totals = store.load_totals(batch["batch_id"])
        inputs = store.load_inputs(batch["batch_id"])
        out.append({
            "batch_id": batch["batch_id"],
            "run_id": batch["run_id"],
            "source": batch["source"],
            "status": batch["status"],
            "created_at": batch["created_at"],
            "finished_at": batch.get("finished_at"),
            "input_digest": batch["input_digest"],
            "watermark_utc": batch.get("watermark_utc"),
            "max_event_time": batch.get("max_event_time"),
            "note": batch.get("note", ""),
            "files": [{"name": f["name"], "sha256": f["sha256"], "lines": f["lines"]}
                      for f in inputs],
            "counters": {
                "raw_lines": _int(totals.get("raw_lines")),
                "applied": _int(totals.get("applied")),
                "duplicates": _int(totals.get("duplicates")),
                "conflicts": _int(totals.get("conflicts")),
                "quarantine": _int(totals.get("quarantine")),
                "late_events": _int(totals.get("late_events")),
                "snapshot_keys": _int(totals.get("snapshot_keys")),
            },
            "reconcile_ok": totals.get("reconcile_ok") == "1",
            "params": json.loads(batch.get("params_json") or "{}"),
        })
    return out


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def detail(store, batch_id: str) -> dict:
    batch = store.get_batch(batch_id)
    if batch is None:
        return {}
    totals = store.load_totals(batch_id)
    reconcile = json.loads(totals.get("reconcile_json") or "[]")
    return {
        "batch_id": batch_id,
        "run_id": batch["run_id"],
        "source": batch["source"],
        "status": batch["status"],
        "created_at": batch["created_at"],
        "finished_at": batch.get("finished_at"),
        "input_digest": batch["input_digest"],
        "watermark_utc": batch.get("watermark_utc"),
        "max_event_time": batch.get("max_event_time"),
        "counters": {k: v for k, v in totals.items()
                     if not k.startswith("reconcile")},
        "reconcile": reconcile,
        "inputs": store.load_inputs(batch_id),
        "stages": store.load_stages(batch_id),
    }


def timeline_rows(store, limit: int = 12) -> list[dict]:
    """批次页顶部的小刻度条：每个批次一次刻度，位置按水位先后。"""
    out = []
    for batch in store.list_batches(limit=limit):
        totals = store.load_totals(batch["batch_id"])
        out.append({
            "batch_id": batch["batch_id"],
            "watermark": batch.get("watermark_utc"),
            "max": batch.get("max_event_time"),
            "applied": totals.get("applied"),
            "late": totals.get("late_events"),
            "status": batch["status"],
        })
    return sorted(out, key=lambda r: r["watermark"] or "")


def watermark_text(store, batch_id: str) -> str:
    batch = store.get_batch(batch_id) or {}
    return iso_utc(parse_iso(batch.get("watermark_utc"))) or "—"
