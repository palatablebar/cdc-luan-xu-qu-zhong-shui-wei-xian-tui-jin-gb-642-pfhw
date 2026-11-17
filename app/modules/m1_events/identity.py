"""规范身份：让批次身份与每行顺序依据只取决于业务内容，不取决于物理行序。

上游可能把同一逻辑批次乱序重发：文件字节变了、行号变了，但业务事实没变。
本模块给出两件东西：

1. ``content_digest`` —— 与文件名、行号、到达次序、物理字节都无关的内容摘要。
   同一批业务事实无论怎么换序重发，摘要恒定，因此只形成一个 batch_id；
2. ``canonical_order`` —— 每条规范事件的确定性次序（canonical_seq）。
   去重首见、水位迟到、版本折叠、快照物化全部以规范序为唯一顺序依据，
   物理接收次序（arrival_seq）只作证据保留，不参与任何判定。

排序键刻意不包含 row_ref / lineno：那两者会随行序漂移，不能进入身份。
并列时用内容自身的规范化哈希兜底，保证全序确定且可复算。
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

from ..m2_schema.service import Norm
from .reader import RawRow

DIGEST_ALGO = "sha256"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def raw_identity_unit(row: RawRow) -> dict:
    """采信行的内容单元：坏行没有归一信封，用原始文本固定其内容。

    坏行统一归为 ``unparsable``：无论是末行截断（E_TAIL_TRUNCATED）还是中段坏 JSON
    （E_BAD_JSON），业务事实都是「这段原始文本无法解析」。具体原因码随行位置漂移，
    不能进入批次身份，否则乱序重发会得到不同摘要。
    """
    if row.error:
        return {"kind": "unparsable", "text": row.text}
    raw = row.raw or {}
    # 只取业务信封与负载；文件名/行号/到达序号一律不进身份。
    return {
        "kind": "raw",
        "table": str(raw.get("table") or ""),
        "pk": "" if raw.get("pk") is None else str(raw.get("pk")),
        "version": raw.get("version"),
        "op": str(raw.get("op") or ""),
        "event_time": str(raw.get("event_time") or ""),
        "schema": str(raw.get("schema") or ""),
        "payload": _stable(raw.get("payload")
                           if isinstance(raw.get("payload"), dict)
                           else {k: v for k, v in raw.items()
                                 if k not in _ENVELOPE_KEYS and k != "payload"}),
        "cells": list(raw.get("__cells") or []),
    }


_ENVELOPE_KEYS = frozenset(
    ("table", "pk", "version", "op", "event_time", "schema", "payload", "__cells"))


def norm_identity_unit(norm: Norm) -> dict:
    """规范事件的内容单元：归一后的信封、UTC 时刻、规范化 payload 与金额。"""
    if norm.errors:
        # 契约失败行：身份只固定在原始文本上（不可解析这一事实），不含随位置漂移的
        # 具体原因码（末行截断 vs 中段坏 JSON），保证乱序重发摘要恒定。
        return {"kind": "unparsable", "text": norm.raw_text}
    return {
        "kind": "event",
        "table": norm.table,
        "pk": norm.pk,
        "version": norm.version,
        "op": norm.op or "",
        "event_time": norm.time_iso or "",
        "schema": norm.schema,
        "payload": _stable(norm.payload),
        "amount": None if norm.amount is None else str(norm.amount),
        "qty": norm.qty,
    }


def _stable(value) -> object:
    """把 dict/list 变成键排序、可确定性序列化的结构。"""
    if isinstance(value, dict):
        return {str(k): _stable(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_stable(v) for v in value]
    return value


def canonical_json(unit: dict) -> str:
    return json.dumps(_stable(unit), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def content_hash(unit: dict) -> str:
    return _sha256_text(canonical_json(unit))


def content_digest(units: Iterable[dict]) -> str:
    """批次级内容摘要：先按每条内容排序再哈希，故与到达次序无关。"""
    bodies = sorted(canonical_json(u) for u in units)
    h = hashlib.sha256()
    for body in bodies:
        h.update(body.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def canonical_sort_key(norm: Norm) -> tuple:
    """规范序排序键：业务时间 → 表 → 键 → 版本 → 操作 → 原始时间文本 → 内容哈希。

    全部字段来自内容本身，不含 row_ref / seq，因此乱序重发得到完全相同的次序。
    纳入 ``event_time_raw`` 是为了让「同一 UTC 时刻、不同时区写法」的并列也有确定
    先后；末尾用内容哈希兜底，保证除字节级完全相同的物理重复外都是全序确定的。
    """
    time_key = norm.time_iso or "￿"  # 非法/缺时间排到末尾
    return (
        time_key,
        norm.table or "￿",
        norm.pk or "￿",
        norm.version if norm.version is not None else 1 << 30,
        norm.op or "￿",
        norm.event_time_raw or "￿",
        content_hash(norm_identity_unit(norm)),
    )


def assign_canonical_seq(norms: list[Norm]) -> None:
    """就地给每条规范事件分配 1 起的规范序，并回填排序依据说明。"""
    ordered = sorted(norms, key=canonical_sort_key)
    for index, norm in enumerate(ordered, 1):
        norm.canonical_seq = index


def digest_of_raw_rows(rows: list[RawRow]) -> str:
    """采信阶段的内容摘要（M1，尚未归一）：与文件字节/行序无关。"""
    return content_digest(raw_identity_unit(r) for r in rows)


def digest_of_norms(norms: list[Norm]) -> str:
    """归一后的内容摘要（M2 之后）：业务事实的最终身份依据。"""
    return content_digest(norm_identity_unit(n) for n in norms)
