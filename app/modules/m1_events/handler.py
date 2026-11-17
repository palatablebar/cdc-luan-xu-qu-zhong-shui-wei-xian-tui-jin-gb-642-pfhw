"""M1 入口：采信一次导出（全部来源或单个文件），并给出事件流明细。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ... import config, errors
from ...web import Ctx, get_ctx, render
from ..m6_batches import service as batch_service

router = APIRouter(tags=["M1 事件采信"])


@router.post("/api/events/import")
def api_import(payload: dict | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    body = payload or {}
    source = str(body.get("source") or "all").strip()
    if source not in ("", "all") and source not in config.SOURCES:
        raise errors.BadRequest(
            f"未知来源 {source!r}；可选 all / " + " / ".join(config.SOURCES),
            code="E_BAD_SOURCE")
    result = batch_service.run(settings=ctx.settings, store=ctx.store,
                               source=source or "all")
    return {
        "status": result["status"],
        "batch_id": result["batch"]["batch_id"],
        "batch_key": result["batch_key"],
        "run_id": result["run_id"],
        "idempotent_replay": result.get("idempotent_replay", False),
        "replay_of": result.get("replay_of"),
        "note": result.get("note"),
        "elapsed_ms": result["elapsed_ms"],
        "stages": result["stages"],
        "counters": result["counters"],
        "totals": result["totals"],
        "reconciliation": result["reconciliation"],
        "all_balanced": result["all_balanced"],
        "watermark": result["watermark"],
        "inputs": result["inputs"],
    }


@router.get("/api/events")
def api_events(row_class: str | None = None, table: str | None = None,
               limit: int = 200, ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = ctx.current_batch()
    if batch is None:
        return {"batch_id": None, "rows": [], "count": 0}
    rows = ctx.store.load_events(batch["batch_id"], row_class or None)
    if table:
        rows = [r for r in rows if r["table_name"] == table]
    rows = rows[: max(1, min(limit, 2000))]
    return {
        "batch_id": batch["batch_id"],
        "count": len(rows),
        "rows": [{
            "seq": r["seq"], "row_ref": r["row_ref"], "table": r["table_name"],
            "pk": r["pk"], "version": r["version"], "op": r["op"],
            "event_time": r["event_time_utc"], "event_time_raw": r["event_time_raw"],
            "schema": r["schema_version"], "class": r["row_class"],
            "reason_code": r["reason_code"],
            "amount": None if r["amount"] is None else str(r["amount"]),
            "late": r["late"], "detail": r["detail"],
        } for r in rows],
    }


@router.get("/events")
def page_events(request: Request, row_class: str | None = None,
                table: str | None = None, ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch()
    layers = ctx.layers(batch["batch_id"]) if batch else {
        "inputs": [], "events": [], "snapshot": [], "watermarks": [],
        "stages": [], "totals": {}}
    events = layers["events"]
    if row_class:
        events = [e for e in events if e["row_class"] == row_class]
    if table:
        events = [e for e in events if e["table_name"] == table]
    return render(request, "events.html", batch=batch, page="events",
                  events=events[:400], total_events=len(layers["events"]),
                  inputs=layers["inputs"], totals=layers["totals"],
                  row_class=row_class or "", table=table or "",
                  table_list=sorted({e["table_name"] for e in layers["events"]
                                     if e["table_name"]}),
                  page_title="事件流")
