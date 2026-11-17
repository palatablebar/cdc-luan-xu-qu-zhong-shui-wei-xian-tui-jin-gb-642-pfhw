"""M4 水位与迟到：max−窗口、单调、迟到仍应用、半开边界。"""
from __future__ import annotations

import datetime as dt

from app import config
from app.modules.m4_watermark import late, service, watermark
from fixtures import expected as X

ISO = lambda value: value.astimezone(config.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _state(norms):
    return watermark.advance(norms, dt.timedelta(minutes=10))


def test_watermark_is_max_minus_window(run):
    assert run["watermark"]["max_event_time"] == X.MAX_EVENT_TIME
    assert run["watermark"]["final"] == X.WATERMARK
    assert run["totals"]["watermark_utc"] == X.WATERMARK


def test_late_event_still_applied(store, batch_id):
    """TRAP-13：迟到 12 分钟的 v3 仍然应用，只是打上迟到标记。"""
    row = next(r for r in store.load_events(batch_id, "applied")
               if r["row_ref"] == X.LATE_12_MIN_REF)
    assert row["late"] is True
    assert row["version"] == 3
    snap = next(r for r in store.load_snapshot(batch_id)
                if r["table_name"] == "orders" and r["pk"] == "1006")
    assert snap["version"] == 3
    assert snap["late"] is True


def test_late_by_exactly_twelve_minutes(store, batch_id):
    """TRAP-13：落后最大事件 720 秒 = 12 分钟，落在 10 分钟窗口之外。"""
    tick = next(t for t in store.load_watermarks(batch_id)
                if t["row_ref"] == X.LATE_12_MIN_REF)
    assert tick["behind_seconds"] == X.LATE_12_MIN_SECONDS
    assert late.minutes(tick["behind_seconds"]) == 12
    assert late.behind_text(tick["behind_seconds"]) == "12 分钟"


def test_boundary_equal_watermark_is_not_late(norm_factory):
    """TRAP-23：event_time 等于水位线不算迟到（半开区间 [水位线, +inf)）。"""
    norms = [norm_factory(pk="1", event_time="2026-11-15T08:20:00Z"),
             norm_factory(pk="2", event_time="2026-11-15T08:30:00Z"),
             norm_factory(pk="3", event_time="2026-11-15T08:20:00Z")]
    state = _state(norms)
    assert state.ticks[-1].watermark == dt.datetime(2026, 11, 15, 8, 20,
                                                    tzinfo=dt.timezone.utc)
    assert state.ticks[-1].late is False
    assert watermark.is_late(dt.datetime(2026, 11, 15, 8, 19, 59, tzinfo=dt.timezone.utc),
                             state.ticks[-1].watermark) is True


def test_watermark_monotonic(norm_factory):
    """不变量：水位线只前进不回退，迟到事件不改变已推进的水位。"""
    norms = [
        norm_factory(pk="1", event_time="2026-11-15T08:00:00Z"),
        norm_factory(pk="2", event_time="2026-11-15T08:30:00Z"),
        norm_factory(pk="3", event_time="2026-11-15T08:05:00Z"),
        norm_factory(pk="4", event_time="2026-11-15T09:10:00Z"),
        norm_factory(pk="5", event_time="2026-11-15T08:20:00Z"),
    ]
    state = _state(norms)
    marks = [tick.watermark for tick in state.ticks]
    assert marks == sorted(marks)
    assert state.ticks[2].late is True
    assert state.ticks[4].late is True
    assert ISO(state.final) == "2026-11-15T09:00:00Z"


def test_only_applied_events_advance_watermark(store, batch_id):
    """水位序列长度 = 已应用事件数：重复、冲突、隔离都不推进水位。"""
    series = store.load_watermarks(batch_id)
    applied = store.load_events(batch_id, "applied")
    assert len(series) == len(applied) == X.APPLIED


def test_first_event_never_late(norm_factory):
    state = _state([norm_factory(pk="1", event_time="2026-11-15T08:00:00Z")])
    assert state.ticks[0].late is False
    assert state.late == 0
    assert ISO(state.final) == "2026-11-15T07:50:00Z"


def test_late_summary_and_table(store, batch_id):
    """迟到计数与迟到表：总数与排序都对得上。"""
    summary = service.summary(store, batch_id)
    assert summary["late"] == X.LATE_EVENTS
    assert summary["total"] == X.APPLIED
    assert summary["late"] + summary["on_time"] == X.APPLIED
    rows = service.late_table(store, batch_id)
    assert len(rows) == X.LATE_EVENTS
    assert rows[0]["behind_minutes"] >= rows[-1]["behind_minutes"]
    assert rows[0]["row_ref"] == "events_v1_gb.csv:3"


def test_window_table_is_half_open():
    rows = {row["name"]: row for row in late.window_table(10)}
    assert rows["快照窗口"]["side"] == "含右端"
    assert rows["待定窗口"]["side"] == "半开"
    assert "水位线" in rows
    assert late.point_in_window(
        dt.datetime(2026, 11, 15, 8, 52, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 11, 15, 8, 52, tzinfo=dt.timezone.utc)) is True
    class _StubStore:
        def load_watermarks(self, batch_id):
            return [
                {"seq": 1, "row_ref": "a",
                 "event_time_utc": "2026-11-15T08:00:00Z",
                 "watermark_utc": "2026-11-15T07:50:00Z", "late": 0,
                 "behind_seconds": 0},
                {"seq": 2, "row_ref": "b",
                 "event_time_utc": "2026-11-15T08:10:00Z",
                 "watermark_utc": "2026-11-15T08:00:00Z", "late": 0,
                 "behind_seconds": 0},
            ]

    ticks = service.advance_points(_StubStore(), "x")
    assert [row["ref"] for row in ticks] == ["a", "b"]
