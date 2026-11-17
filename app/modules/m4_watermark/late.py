"""迟到判定与展示：迟到事件仍然应用，只打标记。

迟到不是错误：下游快照要能看到「这条进得晚，但已按版本应用」。
每个迟到事件给出落后最大 event_time 的秒数，页面按分钟显示。
"""
from __future__ import annotations

import datetime as dt

from .watermark import Tick


def minutes(seconds: int) -> int:
    return int(round(seconds / 60))


def late_rows(ticks: list[Tick], iso) -> list[dict]:
    rows = []
    for tick in ticks:
        if not tick.late:
            continue
        rows.append({
            "row_ref": tick.row_ref, "event_time": iso(tick.event_time),
            "watermark": iso(tick.watermark), "behind_seconds": tick.behind_seconds,
            "behind_minutes": minutes(tick.behind_seconds),
        })
    return sorted(rows, key=lambda r: -r["behind_seconds"])


def summarize(ticks: list[Tick]) -> dict:
    late = [t for t in ticks if t.late]
    on_time = [t for t in ticks if not t.late]
    worst = max((t.behind_seconds for t in late), default=0)
    return {
        "late": len(late),
        "on_time": len(on_time),
        "total": len(ticks),
        "late_rate": (round(len(late) / len(ticks), 4) if ticks else 0.0),
        "worst_behind_minutes": minutes(worst),
        "boundary_note": "event_time 严格小于水位线才算迟到",
    }


def classify_minutes(seconds: int, window_minutes: int) -> str:
    """迟到分档只用于展示排序，不参与应用判定。"""
    mins = minutes(seconds)
    if mins <= 0:
        return "on_time"
    if mins <= window_minutes:
        return "within_window"
    return "beyond_window"


def window_table(window_minutes: int) -> list[dict]:
    return [
        {"name": "应用窗口", "expr": "event_time < 水位线 → 迟到但应用", "side": "半开"},
        {"name": "水位线", "expr": f"max(event_time) − {window_minutes} 分钟", "side": "标量"},
        {"name": "快照窗口", "expr": "event_time ≤ as_of → 进快照", "side": "含右端"},
        {"name": "待定窗口", "expr": "(as_of, +inf) → 本次不进快照", "side": "半开"},
    ]


def behind_text(seconds: int) -> str:
    if seconds <= 0:
        return "—"
    mins = minutes(seconds)
    if mins < 60:
        return f"{mins} 分钟"
    return f"{mins // 60} 小时 {mins % 60} 分钟"


def point_in_window(event_time: dt.datetime, as_of: dt.datetime) -> bool:
    """快照窗口与待定窗口的分界：等于 as_of 归快照。"""
    return event_time <= as_of
