"""快照物化：按水位切出「本次确定」的键状态，剩余键进待定窗口。

窗口划分（互不重叠、合起来覆盖全部已应用事件）：

- 快照窗口：event_time ≤ as_of（含等号一端）
- 待定窗口：(as_of, +inf)（半开，TRAP-23 的端点归属）

同一键既有快照内事件又有待定事件时，快照取 `≤ as_of` 的最大版本，并把该键记入
pending 标记；这类键的金额差记入 drift，使金额身份保持精确。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..m2_schema.coerce import iso_utc
from . import apply as apply_mod


@dataclass
class Snapshot:
    as_of: object
    rows: list[dict] = field(default_factory=list)
    counters: dict = field(default_factory=dict)
    amounts: dict = field(default_factory=dict)

    def totals(self) -> dict:
        out = {"snapshot_as_of": iso_utc(self.as_of)}
        out.update({f"snapshot_{k}": v for k, v in self.counters.items()})
        out.update({f"amount_{k}": self.amounts.get(k, Decimal("0.00"))
                    for k in ("snapshot", "pending", "drift", "applied")})
        return out


def materialize(applied: list[apply_mod.Applied], as_of) -> Snapshot:
    states = apply_mod.build_states(applied)
    rows: list[dict] = []
    counters = {"keys_total": len(states), "snapshot_keys": 0, "active_keys": 0,
                "tombstone_keys": 0, "pending_only_keys": 0, "pending_events": 0,
                "drift_keys": 0}
    snap_amount = Decimal("0.00")
    pending_amount = Decimal("0.00")
    drift_amount = Decimal("0.00")
    applied_amount = apply_mod.total_amount(states)

    for key in sorted(states):
        item = states[key]
        inside = [e for e in item.events if e.event_time <= as_of]
        pending_events = [e for e in item.events if e.event_time > as_of]
        counters["pending_events"] += len(pending_events)
        if not inside:
            counters["pending_only_keys"] += 1
            if not item.tombstone and item.state.amount is not None:
                pending_amount += item.state.amount
            continue
        state = sorted(inside, key=lambda e: (e.version, e.seq))[-1]
        if state.op == "delete":
            counters["tombstone_keys"] += 1
            row_amount = None
            payload = {}
        else:
            counters["active_keys"] += 1
            row_amount = state.amount
            payload = dict(state.payload)
            if state.amount is not None:
                snap_amount += state.amount
        if pending_events:
            counters["drift_keys"] += 1
            final = item.state
            if not final.tombstone and final.amount is not None:
                drift_amount += final.amount
            if row_amount is not None:
                drift_amount -= row_amount
        counters["snapshot_keys"] += 1
        rows.append({
            "table": item.table, "pk": item.pk, "version": state.version,
            "op": state.op, "amount": row_amount, "qty": state.qty,
            "event_time": iso_utc(state.event_time), "row_ref": state.row_ref,
            "late": state.late, "pending": bool(pending_events),
            "payload": payload,
        })
    return Snapshot(
        as_of=as_of, rows=rows, counters=counters,
        amounts={"snapshot": snap_amount, "pending": pending_amount,
                 "drift": drift_amount, "applied": applied_amount},
    )


def reconcile(snapshot: Snapshot) -> list[dict]:
    """三条身份：行账、键账、金额账。任一条不平就显式写出来。"""
    c = snapshot.counters
    a = snapshot.amounts
    rows = [
        {"name": "键账", "kind": "count", "left": "已应用键", "left_value": c["keys_total"],
         "right": "快照键 + 待定键",
         "right_value": c["snapshot_keys"] + c["pending_only_keys"],
         "ok": c["keys_total"] == c["snapshot_keys"] + c["pending_only_keys"]},
        {"name": "快照账", "kind": "count", "left": "快照键", "left_value": c["snapshot_keys"],
         "right": "活跃 + 墓碑",
         "right_value": c["active_keys"] + c["tombstone_keys"],
         "ok": c["snapshot_keys"] == c["active_keys"] + c["tombstone_keys"]},
        {"name": "金额账", "kind": "money", "left": "已应用金额", "left_value": a["applied"],
         "right": "快照 + 待定 + 变化",
         "right_value": a["snapshot"] + a["pending"] + a["drift"],
         "ok": a["applied"] == a["snapshot"] + a["pending"] + a["drift"]},
    ]
    return rows


def pending_rows(applied: list[apply_mod.Applied], as_of) -> list[dict]:
    states = apply_mod.build_states(applied)
    out = []
    for key in sorted(states):
        item = states[key]
        if any(e.event_time > as_of for e in item.events):
            out.append({
                "key": f"{item.table}/{item.pk}", "version": item.state.version,
                "amount": None if item.state.amount is None else str(item.state.amount),
                "row_ref": item.state.row_ref,
                "event_time": iso_utc(item.state.event_time),
                "pending_events": sum(1 for e in item.events if e.event_time > as_of),
            })
    return out
