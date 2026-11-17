"""M4 水位与迟到：max−窗口、最终水位唯一标尺、迟到仍应用、半开边界、顺序无关。"""
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
    """TRAP-13：早于最终水位的 v3 仍然应用，只是打上迟到标记。"""
    row = next(r for r in store.load_events(batch_id, "applied")
               if r["row_ref"] == X.LATE_12_MIN_REF)
    assert row["late"] is True
    assert row["version"] == 3
    snap = next(r for r in store.load_snapshot(batch_id)
                if r["table_name"] == "orders" and r["pk"] == "1006")
    assert snap["version"] == 3
    assert snap["late"] is True


def test_late_judged_against_final_watermark(store, batch_id):
    """顺序无关口径：behind = 最大 event_time − 本事件 event_time，以最终水位判迟到。"""
    tick = next(t for t in store.load_watermarks(batch_id)
                if t["row_ref"] == X.LATE_12_MIN_REF)
    assert tick["behind_seconds"] == X.LATE_12_MIN_SECONDS
    assert late.minutes(tick["behind_seconds"]) == 22
    assert late.behind_text(tick["behind_seconds"]) == "22 分钟"


def test_boundary_equal_watermark_is_not_late(norm_factory):
    """TRAP-23：event_time 等于最终水位线不算迟到（半开区间 [水位线, +inf)）。"""
    norms = [norm_factory(pk="1", event_time="2026-11-15T08:20:00Z"),
             norm_factory(pk="2", event_time="2026-11-15T08:30:00Z"),
             norm_factory(pk="3", event_time="2026-11-15T08:20:00Z")]
    state = _state(norms)
    # 最终水位 = 08:30 − 10 = 08:20；等于 08:20 的两条不迟到。
    assert ISO(state.final) == "2026-11-15T08:20:00Z"
    assert all(not t.late for t in state.ticks)
    assert watermark.is_late(dt.datetime(2026, 11, 15, 8, 19, 59, tzinfo=dt.timezone.utc),
                             state.final) is True


def test_watermark_ticks_monotonic_in_canonical_order(norm_factory):
    """展示刻度按规范序单调不降；迟到集合与接收次序无关。"""
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
    # 最终水位 = 09:10 − 10 = 09:00；严格小于它的四条全部迟到，09:10 不迟到。
    assert ISO(state.final) == "2026-11-15T09:00:00Z"
    assert state.late == 4 and state.on_time == 1
    # 刻度带规范序，且最晚事件 09:10 不迟到。
    assert [t.canonical_seq for t in state.ticks] == sorted(t.canonical_seq
                                                            for t in state.ticks)
    latest = next(t for t in state.ticks
                  if ISO(t.event_time) == "2026-11-15T09:10:00Z")
    assert not latest.late


def test_late_result_independent_of_arrival_order(norm_factory):
    """乱序重发：改变物理接收次序，迟到条数、最终水位、每事件 behind 完全一致。"""
    def make(seq, pk, when):
        n = norm_factory(pk=pk, event_time=when)
        n.seq = seq
        return n

    times = ["2026-11-15T08:00:00Z", "2026-11-15T08:30:00Z",
             "2026-11-15T08:05:00Z", "2026-11-15T09:10:00Z"]
    forward = [make(i + 1, str(i), t) for i, t in enumerate(times)]
    backward = list(reversed(forward))
    a, b = _state(forward), _state(backward)
    assert ISO(a.final) == ISO(b.final)
    assert a.late == b.late and a.on_time == b.on_time

    def by_ref(state):
        return {t.row_ref: (t.late, t.behind_seconds) for t in state.ticks}

    assert by_ref(a) == by_ref(b)


def test_only_applied_events_advance_watermark(store, batch_id):
    """水位序列长度 = 已应用事件数：重复、冲突、隔离都不推进水位。"""
    series = store.load_watermarks(batch_id)
    applied = store.load_events(batch_id, "applied")
    assert len(series) == len(applied) == X.APPLIED


def test_first_event_never_late_alone(norm_factory):
    """单事件批次：最终水位在它之前 10 分钟，自身不迟到。"""
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
                {"seq": 1, "canonical_seq": 1, "row_ref": "a",
                 "event_time_utc": "2026-11-15T08:00:00Z",
                 "watermark_utc": "2026-11-15T07:50:00Z", "late": 0,
                 "behind_seconds": 0},
                {"seq": 2, "canonical_seq": 2, "row_ref": "b",
                 "event_time_utc": "2026-11-15T08:10:00Z",
                 "watermark_utc": "2026-11-15T08:00:00Z", "late": 0,
                 "behind_seconds": 0},
            ]

    ticks = service.advance_points(_StubStore(), "x")
    assert [row["ref"] for row in ticks] == ["a", "b"]
