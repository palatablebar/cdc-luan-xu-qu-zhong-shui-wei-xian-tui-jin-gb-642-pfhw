"""应用：每个键按 (version, 到达序) 升序折叠成唯一状态。

规则：

- 版本大的覆盖版本小的，与到达顺序无关；
- delete 写墓碑（payload 清空，只留键与版本）；
- 墓碑之后的低版本 upsert 已在 M3 判为冲突，这里不会出现，也不会复活；
- 高版本 upsert 可以把墓碑改回活跃（正常重建，不算复活）。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from ..m2_schema.coerce import iso_utc, parse_iso
from ..m2_schema.service import Norm


@dataclass
class Applied:
    table: str
    pk: str
    version: int
    op: str
    payload: dict
    amount: Decimal | None
    qty: int | None
    event_time: dt.datetime
    row_ref: str
    late: bool
    seq: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.table, self.pk)

    @property
    def tombstone(self) -> bool:
        return self.op == "delete"

    @classmethod
    def from_norm(cls, norm: Norm) -> "Applied":
        return cls(table=norm.table, pk=norm.pk, version=int(norm.version or 0),
                   op=norm.op or "upsert", payload=dict(norm.payload),
                   amount=norm.amount, qty=norm.qty, event_time=norm.event_time,
                   row_ref=norm.row_ref, late=norm.late, seq=norm.seq)

    @classmethod
    def from_row(cls, row: dict) -> "Applied":
        return cls(table=row["table_name"], pk=row["pk"], version=int(row["version"]),
                   op=row["op"], payload=dict(row.get("payload") or {}),
                   amount=row.get("amount"), qty=row.get("qty"),
                   event_time=parse_iso(row["event_time_utc"]),
                   row_ref=row["row_ref"], late=bool(row.get("late")), seq=row["seq"])

    def as_dict(self) -> dict:
        return {
            "table": self.table, "pk": self.pk, "version": self.version,
            "op": self.op, "payload": self.payload,
            "amount": None if self.amount is None else str(self.amount),
            "qty": self.qty, "event_time": iso_utc(self.event_time),
            "row_ref": self.row_ref, "late": self.late,
        }


@dataclass
class KeyState:
    table: str
    pk: str
    state: Applied
    events: list[Applied] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.table, self.pk)

    @property
    def versions(self) -> list[int]:
        return sorted({e.version for e in self.events})

    @property
    def late_count(self) -> int:
        return sum(1 for e in self.events if e.late)

    @property
    def tombstone(self) -> bool:
        return self.state.tombstone


def build_states(applied: list[Applied]) -> dict[tuple[str, str], KeyState]:
    groups: dict[tuple[str, str], list[Applied]] = {}
    for item in applied:
        groups.setdefault(item.key, []).append(item)
    states: dict[tuple[str, str], KeyState] = {}
    for key, items in groups.items():
        ordered = sorted(items, key=lambda e: (e.version, e.seq))
        state = ordered[-1]
        if state.op == "delete":
            state = Applied(table=state.table, pk=state.pk, version=state.version,
                            op="delete", payload={}, amount=None, qty=None,
                            event_time=state.event_time, row_ref=state.row_ref,
                            late=state.late, seq=state.seq)
        states[key] = KeyState(table=key[0], pk=key[1], state=state, events=ordered)
    return states


def state_rows(states: dict[tuple[str, str], KeyState]) -> list[dict]:
    rows = []
    for key in sorted(states):
        item = states[key]
        rows.append({
            "key": f"{item.table}/{item.pk}", "table": item.table, "pk": item.pk,
            "version": item.state.version, "op": item.state.op,
            "amount": None if item.state.amount is None else str(item.state.amount),
            "versions": item.versions, "events": len(item.events),
            "late": item.late_count, "tombstone": item.tombstone,
            "row_ref": item.state.row_ref,
        })
    return rows


def total_amount(states: dict[tuple[str, str], KeyState]) -> Decimal:
    total = Decimal("0.00")
    for item in states.values():
        if item.tombstone or item.state.amount is None:
            continue
        total += item.state.amount
    return total
