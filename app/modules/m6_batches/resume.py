"""续跑与重放：断点计划、幂等重放判定、陈旧批次拒绝。

三种情形分开：

1. 同一输入重跑（batch_key 命中已完成批次）→ 复用结果，不新增行；
2. 失败批次续跑 → 沿用原水位，输入重新采信；
3. 输入比已持久化水位更旧 → 拒绝（409），不让它覆盖已推进的状态。
"""
from __future__ import annotations

from ... import errors, reasons
from ..m2_schema.coerce import parse_iso
from . import state


def replay_of(store, batch_key: str) -> dict | None:
    found = store.find_done_by_key(batch_key)
    if not found:
        return None
    return {"batch_id": found["batch_id"], "run_id": found["run_id"],
            "watermark_utc": found.get("watermark_utc"),
            "code": "B_IDEMPOTENT_REPLAY"}


def plan(store, batch_id: str) -> dict:
    batch = store.get_batch(batch_id)
    if batch is None:
        raise errors.NotFound(f"批次 {batch_id} 不存在", code="E_NOT_FOUND")
    latest = store.latest_batch()
    stages = store.load_stages(batch_id)
    failed = [s for s in stages if s["status"] == "failed"]
    if batch["status"] == "done":
        return {"batch_id": batch_id, "can_resume": False,
                "reason": "批次已完成，重跑走幂等重放", "code": "B_IDEMPOTENT_REPLAY",
                "from_stage": None, "watermark_utc": batch.get("watermark_utc"),
                "is_latest": latest is not None and latest["batch_id"] == batch_id}
    if latest is not None and latest["batch_id"] != batch_id:
        older = parse_iso(batch.get("max_event_time"))
        newest = parse_iso(latest.get("max_event_time"))
        if older is not None and newest is not None and older < newest:
            return {"batch_id": batch_id, "can_resume": False,
                    "reason": "该批次不是最新批次，按陈旧批次处理", "code": "C_STALE_BATCH",
                    "from_stage": None, "watermark_utc": batch.get("watermark_utc"),
                    "is_latest": False}
    from_stage = failed[0]["stage"] if failed else "ingest"
    return {"batch_id": batch_id, "can_resume": True,
            "reason": "从失败阶段续跑", "code": "B_RESUMED",
            "from_stage": from_stage, "watermark_utc": batch.get("watermark_utc"),
            "is_latest": True}


def ensure_not_stale(store, max_event_time) -> None:
    stored = state.stored_watermark(store)
    if state.is_stale(max_event_time, stored):
        raise errors.StaleBatch(
            f"输入最大 event_time 落后于已持久化水位线 {stored}，拒绝覆盖",
            code="C_STALE_BATCH",
            detail={"stored_watermark": stored,
                    "incoming_max": max_event_time,
                    "reason": reasons.label("C_STALE_BATCH")})


def resume_note(plan_row: dict) -> str:
    if plan_row["can_resume"]:
        return f"从 {plan_row['from_stage']} 续跑"
    return plan_row["reason"]
