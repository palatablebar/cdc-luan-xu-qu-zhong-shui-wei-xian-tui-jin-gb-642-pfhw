"""M5 快照物化：窗口边界、墓碑、待定、金额守恒、主键保形、可重复物化。"""
from __future__ import annotations

from decimal import Decimal

from app.modules.m5_snapshot import apply, materialize, service
from fixtures import expected as X


def _row(store, batch_id, table, pk):
    return next(r for r in store.load_snapshot(batch_id)
                if r["table_name"] == table and r["pk"] == pk)


def test_snapshot_counts_and_classes(store, batch_id):
    rows = store.load_snapshot(batch_id)
    summary = service.summarize(store, batch_id)
    assert len(rows) == X.SNAPSHOT_KEYS
    assert summary["active_keys"] == X.ACTIVE_KEYS
    assert summary["tombstone_keys"] == X.TOMBSTONE_KEYS
    assert summary["pending_only"] == X.PENDING_ONLY_KEYS


def test_pending_key_excluded(store, batch_id):
    """TRAP-23：只有 as_of 之后事件（09:02）的键不进快照，进待定窗口。"""
    rows = store.load_snapshot(batch_id)
    assert not [r for r in rows if r["table_name"] == X.PENDING_KEY[0]
                and r["pk"] == X.PENDING_KEY[1]]
    pending = service.pending_rows(store, batch_id)
    assert pending[0]["key"] == "orders/1008"
    assert pending[0]["amount"] == X.AMOUNT_PENDING
    assert pending[0]["pending_events"] == 1


def test_boundary_event_at_as_of_included_once(store, batch_id):
    """TRAP-23：event_time 恰等于 as_of（08:52）归快照一侧，且只算一次。"""
    row = _row(store, batch_id, "shipments", "4002")
    assert row["event_time_utc"] == X.AS_OF
    assert row["pending"] is False
    assert len([r for r in store.load_snapshot(batch_id)
                if r["table_name"] == "shipments" and r["pk"] == "4002"]) == 1


def test_amount_identity_holds(store, batch_id):
    """金额账：快照 + 待定 + 变化 = 已应用（Decimal，2 位）。"""
    summary = service.summarize(store, batch_id)
    snapshot = Decimal(summary["amount_snapshot"])
    pending = Decimal(summary["amount_pending"])
    drift = Decimal(summary["amount_drift"])
    applied = Decimal(summary["amount_applied"])
    assert snapshot == Decimal(X.AMOUNT_SNAPSHOT)
    assert pending == Decimal(X.AMOUNT_PENDING)
    assert drift == Decimal(X.AMOUNT_DRIFT)
    assert applied == Decimal(X.AMOUNT_APPLIED)
    assert snapshot + pending + drift == applied


def test_tombstones_keep_no_amount(store, batch_id):
    for table, pk in X.TOMBSTONE_LIST:
        row = _row(store, batch_id, table, pk)
        assert row["op"] == "delete"
        assert row["amount"] is None
        assert row["payload"] == {}


def test_primary_keys_preserved(store, batch_id):
    """主键保形：超过 2^53 的整数与前导零编码都按原样进快照。"""
    keys = {(r["table_name"], r["pk"]) for r in store.load_snapshot(batch_id)}
    assert ("inventory", X.BIGINT_PK) in keys
    assert ("inventory", X.LEADING_ZERO_PK) in keys


def test_sentinel_amount_row_kept_with_null(store, batch_id):
    """哨兵金额的键仍然进快照，只是金额为空且不计入合计。"""
    row = _row(store, batch_id, *X.SENTINEL_AMOUNT_KEY)
    assert row["op"] == "upsert"
    assert row["amount"] is None


def test_rebuild_is_idempotent(store, batch_id):
    """同一 as_of 重算：行数与合计都不变（物化可重复）。"""
    before = store.load_totals(batch_id)
    rows_before = len(store.load_snapshot(batch_id))
    service.rebuild(store, batch_id)
    service.rebuild(store, batch_id)
    after = store.load_totals(batch_id)
    assert len(store.load_snapshot(batch_id)) == rows_before
    for key in ("amount_snapshot", "amount_pending", "amount_applied",
                "snapshot_keys", "active_keys", "tombstone_keys"):
        assert before[key] == after[key]


def test_state_rows_versions(store, batch_id):
    """键状态保留版本跨度与迟到计数，页面据此复核。"""
    rows = {row["key"]: row for row in service.state_rows(store, batch_id)}
    target = rows["orders/1006"]
    assert target["versions"] == [1, 2, 3]
    assert target["events"] == 3
    assert target["late"] == 1
    assert target["version"] == 3
    assert target["amount"] == "61.00"


def test_reconcile_three_accounts(store, batch_id):
    rows = service.reconcile_rows(store, batch_id)
    assert [row["name"] for row in rows] == ["键账", "快照账", "金额账"]
    assert all(row["ok"] for row in rows)
    empty = materialize.materialize([], None)
    assert empty.counters["keys_total"] == 0
    assert all(row["ok"] for row in materialize.reconcile(empty))
    assert apply.build_states([]) == {}
