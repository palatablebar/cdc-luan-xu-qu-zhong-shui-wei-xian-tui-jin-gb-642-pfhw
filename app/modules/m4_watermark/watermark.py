"""水位线推进：max(event_time) − 迟到窗口，结果与物理到达次序无关。

定义（半开区间，顺序无关版本）：

- **最终水位线** = 本批全部已应用事件的最大 `event_time` − 窗口。它是批次内唯一的
  迟到标尺，是个批次级标量，不随事件到达先后变化；
- `event_time < 最终水位线` 记迟到，**等于水位线不算迟到**；`behind_seconds` =
  最大 event_time − 本事件 event_time（为 0 表示不落后）。因此乱序重发时，迟到集合、
  迟到条数与每条落后秒数完全恒定；
- 水位刻度（Tick）按 **canonical_seq（规范序）** 产出，刻度上的 `watermark` 记录
  「按规范序处理到该事件时的已见最大值 − 窗口」，用于时间轴展示，单调不降；它只用于
  展示推进过程，迟到判定一律以最终水位线为准，不依赖接收次序；
- 水位线只由已应用事件推进（重复、冲突、隔离不进本模块），保证同一业务内容可复算。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ..m2_schema.service import Norm


@dataclass
class Tick:
    seq: int
    canonical_seq: int
    row_ref: str
    event_time: dt.datetime
    watermark: dt.datetime
    late: bool
    behind_seconds: int


@dataclass
class WatermarkState:
    final: dt.datetime | None = None
    max_event_time: dt.datetime | None = None
    ticks: list[Tick] = field(default_factory=list)
    advance_points: list[dict] = field(default_factory=list)

    @property
    def late(self) -> int:
        return sum(1 for t in self.ticks if t.late)

    @property
    def on_time(self) -> int:
        return sum(1 for t in self.ticks if not t.late)

    @property
    def max_behind_seconds(self) -> int:
        return max((t.behind_seconds for t in self.ticks), default=0)


def _order(norm: Norm) -> int:
    return norm.canonical_seq or norm.seq


def advance(events: list[Norm], window: dt.timedelta) -> WatermarkState:
    state = WatermarkState()
    timed = [n for n in events if n.event_time is not None]
    if not timed:
        return state

    # 批次级标量：最终水位与最大事件时间，与遍历/到达顺序完全无关。
    max_event = max(n.event_time for n in timed)
    final_mark = max_event - window
    state.max_event_time = max_event
    state.final = final_mark

    max_seen: dt.datetime | None = None
    last_mark: dt.datetime | None = None
    for norm in sorted(timed, key=_order):
        base = max_seen if max_seen is not None else norm.event_time
        mark = base - window
        # 迟到以「最终水位线」为唯一标尺，顺序无关；等于不算迟到。
        late = norm.event_time < final_mark
        behind = int((max_event - norm.event_time).total_seconds())
        if behind < 0:
            behind = 0
        norm.late = late
        norm.behind_seconds = behind
        tick = Tick(seq=norm.seq, canonical_seq=_order(norm), row_ref=norm.row_ref,
                    event_time=norm.event_time, watermark=mark, late=late,
                    behind_seconds=behind)
        state.ticks.append(tick)
        if last_mark is None or mark > last_mark:
            state.advance_points.append({
                "seq": norm.seq, "canonical_seq": _order(norm),
                "row_ref": norm.row_ref,
                "event_time": norm.event_time, "watermark": mark,
            })
            last_mark = mark
        if max_seen is None or norm.event_time > max_seen:
            max_seen = norm.event_time
    return state


def is_late(event_time: dt.datetime, watermark: dt.datetime) -> bool:
    """半开区间：严格小于才迟到。TRAP-23 的端点归属就在这里。"""
    return event_time < watermark


def series_rows(state: WatermarkState, iso) -> list[dict]:
    rows = []
    for tick in state.ticks:
        rows.append({
            "seq": tick.seq, "canonical_seq": tick.canonical_seq,
            "row_ref": tick.row_ref,
            "event_time": iso(tick.event_time), "watermark": iso(tick.watermark),
            "late": tick.late, "behind_seconds": tick.behind_seconds,
        })
    return rows


def summary(state: WatermarkState, iso) -> dict:
    return {
        "final": iso(state.final),
        "max_event_time": iso(state.max_event_time),
        "late": state.late,
        "on_time": state.on_time,
        "max_behind_seconds": state.max_behind_seconds,
        "applied": len(state.ticks),
    }
