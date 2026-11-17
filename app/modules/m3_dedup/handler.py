"""M3 入口：冲突表、隔离表、原因码分布与优先级表。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...web import Ctx, get_ctx, render
from . import conflict, service

router = APIRouter(tags=["M3 去重冲突"])


@router.get("/api/conflicts")
def api_conflicts(ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = ctx.current_batch()
    if batch is None:
        return {"batch_id": None, "counters": {}, "conflicts": [], "quarantine": []}
    rows = ctx.store.load_events(batch["batch_id"])
    conflicts = [r for r in rows if r["row_class"] == "conflict"]
    quarantine = [r for r in rows if r["row_class"] == "quarantine"]
    duplicates = [r for r in rows if r["row_class"] == "duplicate"]
    return {
        "batch_id": batch["batch_id"],
        "counters": {"applied": sum(1 for r in rows if r["row_class"] == "applied"),
                     "duplicates": len(duplicates), "conflicts": len(conflicts),
                     "quarantine": len(quarantine), "total": len(rows)},
        "conflicts": [_row(r) for r in conflicts],
        "quarantine": [_row(r) for r in quarantine],
        "duplicates": [_row(r) for r in duplicates],
        "groups": conflict.conflict_groups_from_rows(conflicts),
        "distribution": service.distribution_from_rows(rows),
        "priority": service.rule_table(),
    }


@router.get("/conflicts")
def page_conflicts(request: Request, ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch()
    layers = ctx.layers(batch["batch_id"]) if batch else {
        "inputs": [], "events": [], "snapshot": [], "watermarks": [],
        "stages": [], "totals": {}}
    rows = layers["events"]
    conflicts = [r for r in rows if r["row_class"] == "conflict"]
    quarantine = [r for r in rows if r["row_class"] == "quarantine"]
    duplicates = [r for r in rows if r["row_class"] == "duplicate"]
    return render(request, "conflicts.html", batch=batch, page="conflicts",
                  conflicts=conflicts, quarantine=quarantine, duplicates=duplicates,
                  groups=conflict.conflict_groups_from_rows(conflicts),
                  distribution=service.distribution_from_rows(rows),
                  priority=service.rule_table(),
                  dup_groups=_dup_groups(duplicates),
                  totals=layers["totals"], inputs=layers["inputs"],
                  page_title="冲突与隔离")


def _row(row: dict) -> dict:
    return {"row_ref": row["row_ref"], "table": row["table_name"], "pk": row["pk"],
            "version": row["version"], "op": row["op"],
            "event_time": row["event_time_utc"], "reason_code": row["reason_code"],
            "field": row["field"], "raw_value": row["raw_value"],
            "detail": row["detail"], "late": row["late"],
            "class": row["row_class"]}


def _dup_groups(duplicates: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for row in duplicates:
        key = f"{row['table_name']}/{row['pk']}/v{row['version']}"
        groups.setdefault(key, []).append(row)
    out = []
    for key, items in groups.items():
        out.append({"key": key, "count": len(items) + 1,
                    "refs": [i["row_ref"] for i in items][:6],
                    "code": items[0]["reason_code"]})
    return sorted(out, key=lambda r: (-r["count"], r["key"]))
