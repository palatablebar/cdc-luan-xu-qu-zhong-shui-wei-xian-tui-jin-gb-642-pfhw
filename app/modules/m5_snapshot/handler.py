"""M5 入口：物化快照、读快照、待定窗口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...web import Ctx, get_ctx, render
from . import service

router = APIRouter(tags=["M5 快照物化"])


@router.post("/api/snapshot")
def api_snapshot(ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = ctx.current_batch()
    if batch is None:
        return {"batch_id": None, "rows": 0, "summary": {}}
    snapshot = service.rebuild(ctx.store, batch["batch_id"])
    return {
        "batch_id": batch["batch_id"],
        "as_of": service.as_of_text(ctx.store, batch["batch_id"]),
        "rows": len(snapshot.rows),
        "counters": snapshot.counters,
        "amounts": {k: str(v) for k, v in snapshot.amounts.items()},
        "reconcile": [_jsonable(row) for row in service.reconcile_rows(
            ctx.store, batch["batch_id"])],
        "summary": service.summarize(ctx.store, batch["batch_id"]),
    }


@router.get("/api/snapshot")
def api_snapshot_read(ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = ctx.current_batch()
    if batch is None:
        return {"batch_id": None, "rows": [], "summary": {}}
    return {
        "batch_id": batch["batch_id"],
        "as_of": service.as_of_text(ctx.store, batch["batch_id"]),
        "summary": service.summarize(ctx.store, batch["batch_id"]),
        "rows": [{**row, "amount": None if row["amount"] is None else str(row["amount"])}
                 for row in service.page_rows(ctx.store, batch["batch_id"])],
        "pending": service.pending_rows(ctx.store, batch["batch_id"]),
        "reconcile": [_jsonable(row) for row in service.reconcile_rows(
            ctx.store, batch["batch_id"])],
    }


@router.get("/snapshot")
def page_snapshot(request: Request, ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch()
    layers = ctx.layers(batch["batch_id"]) if batch else {
        "inputs": [], "events": [], "snapshot": [], "watermarks": [],
        "stages": [], "totals": {}}
    summary = (service.summarize(ctx.store, batch["batch_id"]) if batch
               else service.blank_summary())
    return render(request, "snapshot.html", batch=batch, page="snapshot",
                  summary=summary,
                  rows=service.page_rows(ctx.store, batch["batch_id"])[:300]
                  if batch else [],
                  pending=service.pending_rows(ctx.store, batch["batch_id"])
                  if batch else [],
                  reconcile=service.reconcile_rows(ctx.store, batch["batch_id"])
                  if batch else [],
                  window_rows=service.window_rows(),
                  totals=layers["totals"], inputs=layers["inputs"],
                  page_title="快照")


def _jsonable(row: dict) -> dict:
    from decimal import Decimal

    out = {}
    for key, value in row.items():
        out[key] = str(value) if isinstance(value, Decimal) else value
    return out
