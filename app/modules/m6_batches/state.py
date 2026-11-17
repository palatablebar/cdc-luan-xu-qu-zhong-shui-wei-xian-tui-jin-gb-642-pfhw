"""批次状态：批次键、参数规范化、水位持久化与陈旧判定。

批次键 = **业务内容摘要** + 规则版本 + 参数 的 sha256 前 10 位，用作 batch_id。
内容摘要（content_digest）按每条采信行的规范化内容排序后哈希，与物理字节、文件名、
行号、到达次序都无关：上游把同一逻辑批次乱序重发，内容摘要不变，命中同一 batch_id，
直接复用已落库结果（幂等），不会产生第二套页面/导出结果。物理文件 sha256 仅作证据
保留在 inputs / manifest 里，不参与身份计算。

水位持久化：水位只允许前进。若本次输入的最大 event_time 低于已持久化水位线，
说明拿到的是旧导出，按陈旧批次拒绝，不让它覆盖已经推进过的状态（TRAP-26）。
"""
from __future__ import annotations

import hashlib
import json

from ... import config
from ..m1_events import identity
from ..m2_schema.coerce import parse_iso
from ..m2_schema.service import Norm


def normalize_params(params: dict | None) -> dict:
    clean = {}
    for key in sorted((params or {})):
        value = params[key]
        if value is None or value == "":
            continue
        clean[str(key)] = value
    return clean


def content_digest_of(norms: list[Norm]) -> str:
    """归一后的业务内容摘要：批次身份的唯一事实依据，乱序重发保持恒定。"""
    return identity.digest_of_norms(norms)


def batch_key(content_digest: str, rule_version: str, params: dict | None = None) -> str:
    body = "|".join([
        content_digest,
        rule_version,
        json.dumps(normalize_params(params), sort_keys=True, ensure_ascii=False),
    ])
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:10]


def batch_id_for(key: str) -> str:
    return f"B-{key}"


def stored_watermark(store) -> str | None:
    return store.watermark_high()


def is_stale(max_event_time, stored) -> bool:
    """输入的最大 event_time 落后于已持久化水位线即为陈旧。"""
    if not stored or not max_event_time:
        return False
    left = parse_iso(max_event_time)
    right = parse_iso(stored)
    if left is None or right is None:
        return False
    return left < right


def watermark_note(stored: str | None, current: str | None) -> dict:
    if not stored:
        return {"state": "first", "stored": None, "current": current}
    if not current:
        return {"state": "kept", "stored": stored, "current": None}
    left, right = parse_iso(current), parse_iso(stored)
    if left is not None and right is not None and left < right:
        return {"state": "regress", "stored": stored, "current": current}
    return {"state": "advance", "stored": stored, "current": current}


def params_text(params: dict | None) -> str:
    clean = normalize_params(params)
    if not clean:
        return "默认"
    return json.dumps(clean, ensure_ascii=False, sort_keys=True)


def window_label(settings) -> str:
    return f"{settings.late_window_minutes} 分钟 / {config.WINDOW_NOTE}"
