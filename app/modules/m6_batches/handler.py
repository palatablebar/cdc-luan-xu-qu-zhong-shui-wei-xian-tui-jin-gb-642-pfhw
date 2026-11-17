"""M6 入口：批次历史、运行清单、续跑与重放。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ... import config, errors, manifest as manifest_mod, reasons
from ...web import Ctx, get_ctx, render
from . import history, resume, service, state

router = APIRouter(tags=["M6 批次续跑"])


@router.get("/api/batches")
def api_batches(limit: int = 30, ctx: Ctx = Depends(get_ctx)) -> dict:
    rows = history.rows(ctx.store, limit=max(1, min(limit, 100)))
    return {"count": len(rows), "batches": rows,
            "stored_watermark": state.stored_watermark(ctx.store)}


@router.get("/api/batches/{batch_id}")
def api_batch(batch_id: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    data = history.detail(ctx.store, batch_id)
    if not data:
        raise errors.NotFound(f"批次 {batch_id} 不存在", code="E_NOT_FOUND")
    data["plan"] = resume.plan(ctx.store, batch_id)
    return data


@router.get("/api/batches/{batch_id}/manifest")
def api_manifest(batch_id: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    doc = ctx.store.load_manifest(batch_id)
    if doc is None:
        raise errors.NotFound(f"批次 {batch_id} 没有运行清单", code="E_NOT_FOUND")
    return doc


@router.post("/api/batches/{batch_id}/resume")
def api_resume(batch_id: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = ctx.store.get_batch(batch_id)
    if batch is None:
        raise errors.NotFound(f"批次 {batch_id} 不存在", code="E_NOT_FOUND")
    result = service.resume_batch(settings=ctx.settings, store=ctx.store,
                                  batch_id=batch_id)
    return {
        "status": result["status"],
        "batch_id": result["batch"]["batch_id"],
        "batch_key": result["batch_key"],
        "run_id": result["run_id"],
        "idempotent_replay": result.get("idempotent_replay", False),
        "replay_of": result.get("replay_of"),
        "resume_plan": result.get("resume_plan"),
        "resume_note": result.get("resume_note"),
        "note": result.get("note"),
        "counters": result["counters"],
        "totals": result["totals"],
        "reconciliation": result["reconciliation"],
        "all_balanced": result["all_balanced"],
        "watermark": result["watermark"],
        "stages": result["stages"],
    }


@router.get("/batches")
def page_batches(request: Request, ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch()
    rows = history.rows(ctx.store, limit=20)
    totals = ctx.store.load_totals(batch["batch_id"]) if batch else {}
    stages = ctx.store.load_stages(batch["batch_id"]) if batch else []
    return render(request, "batches.html", batch=batch, page="batches",
                  batches=rows, totals=totals, stages=stages,
                  stored_watermark=state.stored_watermark(ctx.store),
                  plan=resume.plan(ctx.store, batch["batch_id"]) if batch else {},
                  manifest=(ctx.store.load_manifest(batch["batch_id"])
                            if batch else None),
                  ticks=history.timeline_rows(ctx.store, limit=10),
                  reason_catalog=reasons.catalog(),
                  rule_version=config.RULE_VERSION,
                  page_title="批次")
