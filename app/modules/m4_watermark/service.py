"""水位页编排：把落库的水位序列还原成时间轴刻度与迟到表。"""
from __future__ import annotations

from ... import config
from ..m2_schema.coerce import parse_iso
from . import late, watermark


def _rows(store, batch_id: str) -> list[dict]:
    return store.load_watermarks(batch_id)


def summary(store, batch_id: str) -> dict:
    rows = _rows(store, batch_id)
    if not rows:
        return {"final": None, "max_event_time": None, "late": 0, "on_time": 0,
                "total": 0, "late_rate": 0.0, "worst_behind_minutes": 0,
                "boundary_note": ""}
    final = max(r["watermark_utc"] for r in rows)
    max_event = max(r["event_time_utc"] for r in rows)
    marks = [r["watermark_utc"] for r in rows]
    late_rows = [r for r in rows if r["late"]]
    worst = max((r["behind_seconds"] for r in late_rows), default=0)
    return {
        "final": final,
        "max_event_time": max_event,
        "min_event_time": min(r["event_time_utc"] for r in rows),
        "min_watermark": min(marks),
        "late": len(late_rows),
        "on_time": len(rows) - len(late_rows),
        "total": len(rows),
        "late_rate": round(len(late_rows) / len(rows), 4) if rows else 0.0,
        "worst_behind_minutes": late.minutes(worst),
        "boundary_note": "event_time 严格小于水位线才算迟到",
    }


def timeline(store, batch_id: str) -> dict:
    rows = _rows(store, batch_id)
    if not rows:
        return {"ticks": [], "final": None, "max": None, "min": None,
                "late_window": config.LATE_WINDOW_MINUTES}
    events = [parse_iso(r["event_time_utc"]) for r in rows]
    marks = [parse_iso(r["watermark_utc"]) for r in rows]
    ticks = []
    for row, event_time, mark in zip(rows, events, marks):
        ticks.append({
            "seq": row["seq"], "ref": row["row_ref"],
            "event": event_time.isoformat() if event_time else None,
            "mark": mark.isoformat() if mark else None,
            "late": bool(row["late"]),
            "behind": row["behind_seconds"],
        })
    final = max(marks)
    return {
        "ticks": ticks,
        "final": final.isoformat() if final else None,
        "max": max(events).isoformat() if events else None,
        "min": min(min(events), min(marks)).isoformat() if events else None,
        "late_window": config.LATE_WINDOW_MINUTES,
        "late": sum(1 for r in rows if r["late"]),
        "on_time": sum(1 for r in rows if not r["late"]),
    }


def late_table(store, batch_id: str) -> list[dict]:
    rows = [r for r in _rows(store, batch_id) if r["late"]]
    out = []
    for row in rows:
        out.append({
            "row_ref": row["row_ref"],
            "event_time": row["event_time_utc"],
            "watermark": row["watermark_utc"],
            "behind_minutes": late.minutes(row["behind_seconds"]),
            "behind_text": late.behind_text(row["behind_seconds"]),
            "band": late.classify_minutes(row["behind_seconds"],
                                           config.LATE_WINDOW_MINUTES),
        })
    return sorted(out, key=lambda r: -r["behind_minutes"])


def advance_points(store, batch_id: str) -> list[dict]:
    """水位推进点：只保留真正抬高水位的刻度，时间轴上打点用。"""
    rows = _rows(store, batch_id)
    out = []
    best: str | None = None
    for row in rows:
        if best is None or row["watermark_utc"] > best:
            best = row["watermark_utc"]
            out.append({"seq": row["seq"], "ref": row["row_ref"],
                        "watermark": row["watermark_utc"],
                        "event_time": row["event_time_utc"]})
    return out


def window_rows() -> list[dict]:
    return late.window_table(config.LATE_WINDOW_MINUTES)
