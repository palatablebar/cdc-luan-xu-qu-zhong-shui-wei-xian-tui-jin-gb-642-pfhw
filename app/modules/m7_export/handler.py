"""M7 入口：证据行 / 隔离区 / 冲突表 / 快照 / 运行清单下载。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from ...web import Ctx, get_ctx
from . import service

router = APIRouter(tags=["M7 审计导出"])

CSV_MEDIA = "text/csv; charset=utf-8"
JSON_MEDIA = "application/json; charset=utf-8"


def _csv(store, batch_id: str, kind: str) -> Response:
    data, filename = service.render_csv(store, batch_id, kind)
    return Response(content=data, media_type=CSV_MEDIA,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/api/export/events.csv")
def export_events(ctx: Ctx = Depends(get_ctx)) -> Response:
    return _csv(ctx.store, service._batch(ctx.store, None)["batch_id"], "events")


@router.get("/api/export/quarantine.csv")
def export_quarantine(ctx: Ctx = Depends(get_ctx)) -> Response:
    return _csv(ctx.store, service._batch(ctx.store, None)["batch_id"], "quarantine")


@router.get("/api/export/conflicts.csv")
def export_conflicts(ctx: Ctx = Depends(get_ctx)) -> Response:
    return _csv(ctx.store, service._batch(ctx.store, None)["batch_id"], "conflicts")


@router.get("/api/export/snapshot.csv")
def export_snapshot(ctx: Ctx = Depends(get_ctx)) -> Response:
    return _csv(ctx.store, service._batch(ctx.store, None)["batch_id"], "snapshot")


@router.get("/api/export/manifest.json")
def export_manifest(ctx: Ctx = Depends(get_ctx)) -> Response:
    batch = service._batch(ctx.store, None)
    data, filename = service.render_manifest(ctx.store, batch["batch_id"])
    return Response(content=data, media_type=JSON_MEDIA,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/api/export/snapshot.json")
def export_snapshot_json(ctx: Ctx = Depends(get_ctx)) -> Response:
    batch = service._batch(ctx.store, None)
    data, filename = service.render_snapshot_json(ctx.store, batch["batch_id"])
    return Response(content=data, media_type=JSON_MEDIA,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/api/export")
def export_catalog(ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = service._batch(ctx.store, None)
    return service.summaries(ctx.store, batch["batch_id"])
