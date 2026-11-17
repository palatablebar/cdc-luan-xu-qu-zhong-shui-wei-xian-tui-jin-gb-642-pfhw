"""M4 入口：水位时间轴（首页）与水位查询接口。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request

from ... import config
from ...web import Ctx, get_ctx, render
from ..m6_batches import history as history_mod
from . import service

router = APIRouter(tags=["M4 水位与迟到"])


@router.get("/api/watermark")
def api_watermark(ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = ctx.current_batch()
    if batch is None:
        return {"batch_id": None, "watermark": None, "ticks": [], "late_rows": []}
    return {
        "batch_id": batch["batch_id"],
        "watermark": service.summary(ctx.store, batch["batch_id"]),
        "ticks": service.timeline(ctx.store, batch["batch_id"]),
        "advance": service.advance_points(ctx.store, batch["batch_id"]),
        "late_rows": service.late_table(ctx.store, batch["batch_id"]),
        "window": service.window_rows(),
        "window_minutes": ctx.settings.late_window_minutes,
    }


@router.get("/")
def page_home(request: Request, batch_id: str | None = None,
              ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch(batch_id)
    layers = ctx.layers(batch["batch_id"]) if batch else {
        "inputs": [], "events": [], "snapshot": [], "watermarks": [],
        "stages": [], "totals": {}}
    totals = layers["totals"]
    reconcile = json.loads(totals.get("reconcile_json") or "[]")
    timeline = service.timeline(ctx.store, batch["batch_id"]) if batch else {
        "ticks": [], "final": None, "max": None, "min": None,
        "late_window": ctx.settings.late_window_minutes}
    recent = sorted(layers["events"], key=lambda r: -r["seq"])[:10]
    return render(request, "index.html", batch=batch, page="watermark",
                  totals=totals, stages=layers["stages"], inputs=layers["inputs"],
                  events=recent, total_events=len(layers["events"]),
                  watermark=service.summary(ctx.store, batch["batch_id"]) if batch else {},
                  timeline_json=json.dumps(timeline, ensure_ascii=False)
                  .replace("</", "<\\/"),
                  timeline=timeline,
                  late_rows=service.late_table(ctx.store, batch["batch_id"])[:6]
                  if batch else [],
                  window_rows=service.window_rows(),
                  reconcile=reconcile,
                  batch_list=history_mod.rows(ctx.store, limit=6),
                  late_window=ctx.settings.late_window_minutes,
                  page_title="水位时间轴")
