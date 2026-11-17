"""模板渲染与共享视图上下文：过滤器、导航、当前批次选择。

页面只读 store，不做业务计算；数字与时间格式化统一在这里，避免每页各写一套。
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import Undefined

from . import config, reasons

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

NAV = (
    ("/", "水位", "watermark"),
    ("/events", "事件流", "events"),
    ("/schema", "契约", "schema"),
    ("/conflicts", "冲突隔离", "conflicts"),
    ("/snapshot", "快照", "snapshot"),
    ("/batches", "批次", "batches"),
)

ROW_CLASS_LABELS = {
    "applied": "已应用",
    "duplicate": "重复",
    "conflict": "冲突",
    "quarantine": "隔离",
}
SNAPSHOT_LABELS = {"active": "活跃", "tombstone": "墓碑", "pending": "待定"}
STATUS_LABELS = {"done": "完成", "failed": "失败", "running": "运行中", "stale": "已拒绝"}


@dataclass
class Ctx:
    settings: config.Settings
    store: object

    def current_batch(self, batch_id: str | None = None) -> dict | None:
        if batch_id:
            return self.store.get_batch(batch_id)
        return self.store.latest_batch()

    def layers(self, batch_id: str) -> dict:
        """一次取齐页面需要的只读数据，避免每页各写一套查询。"""
        store = self.store
        return {
            "inputs": store.load_inputs(batch_id),
            "events": store.load_events(batch_id),
            "snapshot": store.load_snapshot(batch_id),
            "watermarks": store.load_watermarks(batch_id),
            "stages": store.load_stages(batch_id),
            "totals": store.load_totals(batch_id),
        }


def get_ctx(request: Request) -> Ctx:
    return request.app.state.ctx


def setup(app) -> None:
    t = Jinja2Templates(directory=str(TEMPLATES_DIR))
    env = t.env
    env.filters["localdt"] = fmt_local_dt
    env.filters["localhm"] = fmt_local_hm
    env.filters["num"] = fmt_num
    env.filters["dec"] = fmt_dec
    env.filters["money"] = fmt_money
    env.filters["state"] = lambda s: ROW_CLASS_LABELS.get(s, s or "—")
    env.filters["snap"] = lambda s: SNAPSHOT_LABELS.get(s, s or "—")
    env.filters["status"] = lambda s: STATUS_LABELS.get(s, s or "—")
    env.filters["level"] = lambda s: reasons.LEVEL_LABELS.get(s, s)
    env.filters["short"] = lambda s: (s or "")[:12]
    env.filters["masked"] = lambda s, n=12: ((s or "")[:n] + "…") if s and len(s) > n else (s or "—")
    env.filters["tojson"] = lambda v, **kw: json.dumps(v, ensure_ascii=False,
                                                      default=str, **kw)
    env.globals.update(
        nav=NAV,
        stage_keys=config.STAGES,
        stage_labels=config.STAGE_LABELS,
        stage_flow=config.STAGE_FLOW,
        row_class_labels=ROW_CLASS_LABELS,
        snapshot_labels=SNAPSHOT_LABELS,
        reason_catalog=reasons.catalog(),
        reasons=reasons.REASONS,
        level_labels=reasons.LEVEL_LABELS,
        tables=config.TABLES,
        fields=config.FIELDS,
        aliases=config.ALIASES,
        source_labels=config.SOURCE_LABELS,
        source_keys=("all", "main", "v1_csv", "late"),
        money_unit=config.MONEY_UNIT,
        window_note=config.WINDOW_NOTE,
        late_window=config.LATE_WINDOW_MINUTES,
        generated_at=now_local().strftime("%Y-%m-%d %H:%M:%S"),
        timezone_label="Asia/Shanghai (+08:00)",
    )
    app.state.templates = t


DEFAULT_TOTALS = {
    "files": 0, "raw_lines": 0, "blank_lines": 0, "header_repeat": 0,
    "applied": 0, "duplicates": 0, "conflicts": 0, "quarantine": 0,
    "late_events": 0, "on_time_events": 0, "max_behind_minutes": 0,
    "keys_total": 0, "snapshot_keys": 0, "active_keys": 0,
    "tombstone_keys": 0, "pending_only_keys": 0, "pending_events": 0,
    "drift_keys": 0, "amount_snapshot": "0.00", "amount_pending": "0.00",
    "amount_drift": "0.00", "amount_applied": "0.00",
    "reconcile_ok": "0",
}


def render(request: Request, name: str, **context):
    ctx: Ctx = request.app.state.ctx
    totals = context.pop("totals", None)
    context["totals"] = {**DEFAULT_TOTALS, **(totals or {})}
    watermark = context.get("watermark")
    if watermark is None:
        context["watermark"] = {"final": None, "max_event_time": None,
                                "late": 0, "on_time": 0}
    batch = context.pop("batch", None)
    if batch is None:
        batch = ctx.current_batch(context.get("batch_id"))
    base = {
        "request": request,
        "ctx": ctx,
        "settings": ctx.settings,
        "batch": batch,
        "rule_version": ctx.settings.rule_version,
        "print_ts": now_local().strftime("%Y-%m-%d %H:%M"),
    }
    base.update(context)
    return request.app.state.templates.TemplateResponse(
        request=request, name=name, context=base)


def _blank(value) -> bool:
    """未定义 / None / 空串统一按「—」输出，空态页面不会因缺字段 500。"""
    if value is None or isinstance(value, Undefined):
        return True
    return isinstance(value, str) and not value.strip()


def now_local() -> dt.datetime:
    return dt.datetime.now(config.CHINA_TZ)


def to_local(value) -> dt.datetime | None:
    if _blank(value):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    if not isinstance(value, dt.datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=config.UTC)
    return value.astimezone(config.CHINA_TZ)


def fmt_local_dt(value) -> str:
    local = to_local(value)
    return local.strftime("%Y-%m-%d %H:%M:%S") if local else "—"


def fmt_local_hm(value) -> str:
    local = to_local(value)
    return local.strftime("%H:%M") if local else "—"


def fmt_num(value) -> str:
    if _blank(value):
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError, OverflowError):
        return str(value)


def fmt_dec(value) -> str:
    if _blank(value):
        return "—"
    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except Exception:
            return str(value)
    return f"{value:,.2f}"


def fmt_money(value) -> str:
    text = fmt_dec(value)
    return text if text == "—" else f"{text} {config.MONEY_UNIT}"
