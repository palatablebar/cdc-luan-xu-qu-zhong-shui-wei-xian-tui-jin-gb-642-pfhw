"""运行清单：run_id、输入摘要、规则版本、参数、耗时、计数，可复算。"""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid

from . import config


def new_run_id(prefix: str = "CDC") -> str:
    stamp = dt.datetime.now(config.UTC).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:4]}"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_parts(parts: list[str]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def build(*, run_id: str, batch_id: str, batch_key: str, settings, source: str,
          input_digest: str, inputs: list[dict], params: dict, started_at: str,
          finished_at: str, elapsed_ms: int, stages: list[dict], counters: dict,
          watermark: str, max_event_time: str, reconcile: dict) -> dict:
    """清单是一份可独立复核的证据：输入摘要 + 参数 + 计数 + 对账身份。"""
    return {
        "run_id": run_id,
        "batch_id": batch_id,
        "batch_key": batch_key,
        "source": source,
        "rule_version": settings.rule_version,
        "late_window_minutes": settings.late_window_minutes,
        "watermark_utc": watermark,
        "max_event_time_utc": max_event_time,
        "timezone": "入库 UTC，页面按 Asia/Shanghai(+08:00)",
        "money": "金额 Decimal，单位元，最小刻度 0.01",
        "window": config.WINDOW_NOTE,
        "input_digest": input_digest,
        "inputs": inputs,
        "params": params,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_ms": elapsed_ms,
        "stages": stages,
        "counters": counters,
        "reconcile": reconcile,
        "replayable": "同一 input_digest 重跑命中 batch_key，不新增行",
    }
