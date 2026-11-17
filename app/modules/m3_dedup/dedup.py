"""去重：规范化全行签名 + 首见保留。

去重键 = (table, pk, version, op, event_time_utc, 规范化 payload) 的 sha256。
规范化让 "120.5" 与 "120.50" 等价；时间先归 UTC，所以 `...Z` 与 `...+08:00`
写同一时刻的两条会合并（TRAP-11）。
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from ..m2_schema.service import Norm


def canonical_payload(norm: Norm) -> dict:
    out = {}
    for key in sorted(norm.payload):
        value = norm.payload[key]
        if key == "amount":
            amount = norm.amount
            out[key] = None if amount is None else str(amount)
        else:
            text = "" if value is None else str(value).strip()
            out[key] = text
    if norm.amount is not None and "amount" not in out:
        out["amount"] = str(norm.amount)
    return out


def signature(norm: Norm) -> str:
    body = "|".join([
        norm.table,
        norm.pk,
        str(norm.version),
        norm.op or "",
        norm.time_iso or "",
        json.dumps(canonical_payload(norm), sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")),
    ])
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def reason_for_duplicate(first: Norm, later: Norm) -> str:
    if (first.event_time_raw or "") != (later.event_time_raw or ""):
        return "D_TZ_MERGED"
    return "D_EXACT_DUP"


def dedup(events: list[Norm]) -> tuple[list[Norm], list[Norm], dict[int, str], dict[int, str]]:
    """返回 (保留, 重复, seq→原因码, seq→保留行引用)。"""
    seen: dict[str, Norm] = {}
    kept: list[Norm] = []
    dups: list[Norm] = []
    codes: dict[int, str] = {}
    refs: dict[int, str] = {}
    for norm in sorted(events, key=lambda n: n.seq):
        sig = signature(norm)
        first = seen.get(sig)
        if first is None:
            seen[sig] = norm
            kept.append(norm)
            continue
        dups.append(norm)
        codes[norm.seq] = reason_for_duplicate(first, norm)
        refs[norm.seq] = first.row_ref
    return kept, dups, codes, refs


def duplicate_groups(events: list[Norm]) -> list[dict]:
    """同签名的分组视图：页面用来展示「重复了几次、首条在哪」。"""
    groups: dict[str, list[Norm]] = {}
    for norm in sorted(events, key=lambda n: n.seq):
        groups.setdefault(signature(norm), []).append(norm)
    rows = []
    for sig, items in groups.items():
        if len(items) < 2:
            continue
        rows.append({"signature": sig[:12], "count": len(items),
                     "first": items[0].row_ref,
                     "refs": [i.row_ref for i in items],
                     "key": f"{items[0].table}/{items[0].pk}",
                     "version": items[0].version})
    return sorted(rows, key=lambda r: (-r["count"], r["key"]))


def amount_of(norm: Norm) -> Decimal | None:
    return norm.amount
