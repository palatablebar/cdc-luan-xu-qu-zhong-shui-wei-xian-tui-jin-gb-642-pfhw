"""快照编排：从已应用事件重建、落库、给页面/导出提供行。"""
from __future__ import annotations

from decimal import Decimal

from ... import config
from ..m2_schema.coerce import iso_utc, parse_iso
from . import apply as apply_mod
from . import materialize


def applied_events(store, batch_id: str) -> list[apply_mod.Applied]:
    return [apply_mod.Applied.from_row(row)
            for row in store.load_events(batch_id, "applied")]


def as_of_of(store, batch_id: str):
    batch = store.get_batch(batch_id) or {}
    return parse_iso(batch.get("watermark_utc"))


def build(store, batch_id: str) -> materialize.Snapshot:
    events = applied_events(store, batch_id)
    as_of = as_of_of(store, batch_id)
    if as_of is None:
        as_of = max((e.event_time for e in events), default=None)
    return materialize.materialize(events, as_of)


def rebuild(store, batch_id: str) -> materialize.Snapshot:
    """可重复调用：同一 as_of 重算快照，行数不变、合计不变。"""
    snapshot = build(store, batch_id)
    totals = snapshot.totals()
    store.replace_snapshot(batch_id, snapshot.rows, totals)
    return snapshot


def page_rows(store, batch_id: str) -> list[dict]:
    rows = []
    for row in store.load_snapshot(batch_id):
        rows.append({
            "key": f"{row['table_name']}/{row['pk']}", "table": row["table_name"],
            "pk": row["pk"], "version": row["version"],
            "kind": "pending" if row["pending"] else (
                "tombstone" if row["op"] == "delete" else "active"),
            "op": row["op"], "amount": row["amount"], "qty": row["qty"],
            "event_time": row["event_time_utc"], "row_ref": row["row_ref"],
            "late": row["late"], "pending": row["pending"],
            "payload": row["payload"],
        })
    return rows


def blank_summary() -> dict:
    """空态：页面渲染用的零值摘要（金额仍是两位字符串）。"""
    return {
        "as_of": None, "keys_total": 0, "snapshot_keys": 0, "active_keys": 0,
        "tombstone_keys": 0, "pending_keys": 0, "pending_only": 0,
        "pending_events": 0, "drift_keys": 0, "rows": 0,
        "amount_snapshot": Decimal("0.00"), "amount_pending": Decimal("0.00"),
        "amount_applied": Decimal("0.00"), "amount_drift": Decimal("0.00"),
    }


def summarize(store, batch_id: str) -> dict:
    totals = store.load_totals(batch_id)
    rows = store.load_snapshot(batch_id)
    active = sum(1 for r in rows if r["op"] != "delete" and not r["pending"])
    tomb = sum(1 for r in rows if r["op"] == "delete")
    pending = sum(1 for r in rows if r["pending"])
    return {
        "as_of": totals.get("snapshot_as_of"),
        "keys_total": _int(totals.get("keys_total")) or (len(rows)
                                                          + _int(totals.get("pending_only_keys"))),
        "snapshot_keys": _int(totals.get("snapshot_keys")) or len(rows),
        "active_keys": _int(totals.get("active_keys")) or active,
        "tombstone_keys": _int(totals.get("tombstone_keys")) or tomb,
        "pending_keys": pending,
        "pending_only": _int(totals.get("pending_only_keys")),
        "pending_events": _int(totals.get("pending_events")),
        "drift_keys": _int(totals.get("drift_keys")),
        "amount_snapshot": _dec(totals.get("amount_snapshot")),
        "amount_pending": _dec(totals.get("amount_pending")),
        "amount_applied": _dec(totals.get("amount_applied")),
        "amount_drift": _dec(totals.get("amount_drift")),
        "rows": len(rows),
    }


def reconcile_rows(store, batch_id: str) -> list[dict]:
    events = applied_events(store, batch_id)
    as_of = as_of_of(store, batch_id)
    if as_of is None and events:
        as_of = max(e.event_time for e in events)
    return materialize.reconcile(materialize.materialize(events, as_of))


def pending_rows(store, batch_id: str) -> list[dict]:
    events = applied_events(store, batch_id)
    as_of = as_of_of(store, batch_id)
    if as_of is None and events:
        as_of = max(e.event_time for e in events)
    return materialize.pending_rows(events, as_of)


def state_rows(store, batch_id: str) -> list[dict]:
    return apply_mod.state_rows(apply_mod.build_states(applied_events(store, batch_id)))


def money(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0.00")
    return Decimal(str(value))


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _dec(value) -> Decimal:
    return money(value)


def window_rows() -> list[dict]:
    return [
        {"side": "含右端", "expr": "event_time ≤ as_of → 进快照",
         "note": f"as_of = 最大 event_time − {config.LATE_WINDOW_MINUTES} 分钟"},
        {"side": "半开", "expr": "(as_of, +inf) → 待定",
         "note": "待定键本次不入快照"},
    ]


def as_of_text(store, batch_id: str) -> str:
    value = as_of_of(store, batch_id)
    return iso_utc(value) or "—"
