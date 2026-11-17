"""应用装配：配置 → 存储 → 模板 → 七个功能模块路由。

用工厂函数而不是模块级 app，测试可以给每个用例一个临时库与临时样例目录。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import bootstrap, config, errors, web
from .store import Store
from .modules.m1_events import handler as m1_handler
from .modules.m2_schema import handler as m2_handler
from .modules.m3_dedup import handler as m3_handler
from .modules.m4_watermark import handler as m4_handler
from .modules.m5_snapshot import handler as m5_handler
from .modules.m6_batches import handler as m6_handler
from .modules.m7_export import handler as m7_handler

MODULES = (m1_handler, m2_handler, m3_handler, m4_handler, m5_handler,
           m6_handler, m7_handler)


def create_app(settings: config.Settings | None = None,
               *, seed: bool | None = None) -> FastAPI:
    settings = settings or config.Settings.from_env()
    if seed is None:
        seed = settings.seed_on_start

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if seed:
            bootstrap.ensure_seed(app)
        yield

    app = FastAPI(
        title="cdcwater CDC 乱序去重与水位线推进",
        description="采信 → 契约 → 去重冲突 → 水位迟到 → 快照 → 导出",
        version=settings.rule_version,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = Store(settings.db_path)
    app.state.ctx = web.Ctx(settings=settings, store=app.state.store)
    web.setup(app)
    app.mount("/static", StaticFiles(directory=str(web.STATIC_DIR)), name="static")
    for module in MODULES:
        app.include_router(module.router)
    errors.install_handlers(app)

    @app.get("/api/health", include_in_schema=False)
    def health() -> dict:
        store = app.state.store
        batch = store.latest_batch()
        totals = store.load_totals(batch["batch_id"]) if batch else {}
        return {
            "status": "ok",
            "rule_version": settings.rule_version,
            "late_window_minutes": settings.late_window_minutes,
            "batch_id": batch["batch_id"] if batch else None,
            "watermark_utc": batch.get("watermark_utc") if batch else None,
            "events": int(totals.get("raw_lines", 0) or 0),
            "applied": int(totals.get("applied", 0) or 0),
            "snapshot_keys": int(totals.get("snapshot_keys", 0) or 0),
        }

    @app.exception_handler(KeyError)
    async def _key_error(request: Request, exc: KeyError):  # noqa: ANN001
        return JSONResponse(status_code=400,
                            content={"error": "E_BAD_REQUEST",
                                     "message": f"缺少字段 {exc}", "detail": {}})

    return app
