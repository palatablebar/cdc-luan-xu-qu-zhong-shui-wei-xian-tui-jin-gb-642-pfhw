"""M3 去重与冲突：签名去重、版本冲突、墓碑复活、优先级与顺序无关。"""
from __future__ import annotations

from app.modules.m3_dedup import conflict, dedup, service
from fixtures import expected as X


def _by_ref(rows, ref):
    return next(r for r in rows if r["row_ref"] == ref)


def test_exact_duplicate_keeps_first(store, batch_id):
    rows = store.load_events(batch_id, "duplicate")
    first = _by_ref(rows, "events_main.jsonl:5")
    assert first["reason_code"] == "D_EXACT_DUP"
    assert first["detail"].endswith("events_main.jsonl:4 同签名")
    assert len(rows) == X.DUPLICATES


def test_timezone_written_duplicate_merged(store, batch_id):
    """TRAP-11：08:10Z 与 16:10+08:00 同一时刻，按重复合并并注明。"""
    row = _by_ref(store.load_events(batch_id, "duplicate"), "events_main.jsonl:9")
    assert row["reason_code"] == "D_TZ_MERGED"


def test_same_version_two_payloads_both_conflict(store, batch_id):
    """TRAP-14：同键同版本两种内容 → 两条都不应用。"""
    rows = [_by_ref(store.load_events(batch_id, "conflict"), ref)
            for ref in X.VERSION_CONFLICT_REFS]
    for row in rows:
        assert row["reason_code"] == "C_VERSION_CONFLICT"
    applied = store.load_events(batch_id, "applied")
    assert not [r for r in applied if r["table_name"] == "orders" and r["pk"] == "1005"]


def test_tombstone_after_delete_conflicts(store, batch_id):
    """TRAP-14：删除（v3）之后到达 v2 更新 → 复活冲突，不改变墓碑状态。"""
    row = _by_ref(store.load_events(batch_id, "conflict"), X.RESURRECT_REF)
    assert row["reason_code"] == "C_TOMBSTONE_RESURRECT"
    assert row["version"] == 2
    assert "删除版本" in row["detail"]


def test_low_version_before_delete_still_applies(store, batch_id):
    """删除之前到达的低版本按版本序应用，最后被墓碑覆盖，不算复活。"""
    applied = [r for r in store.load_events(batch_id, "applied")
               if r["table_name"] == "orders" and r["pk"] == "1001"]
    assert sorted(r["version"] for r in applied) == [1, 2, 3]
    assert [r["op"] for r in applied if r["version"] == 3] == ["delete"]


def test_conflict_beats_duplicate(norm_factory):
    """TRAP-19：X / Y / X 三条同版本事件全判冲突，不能判成 1 冲突 + 1 重复。"""
    payload_x = {"status": "A", "amount": "1.00"}
    payload_y = {"status": "B", "amount": "1.00"}
    norms = [norm_factory(pk="9", version=1, payload=payload_x),
             norm_factory(pk="9", version=1, payload=payload_y),
             norm_factory(pk="9", version=1, payload=payload_x)]
    result = service.classify(norms)
    assert len(result.conflicts) == 3
    assert result.duplicates == []
    assert {n.reason_code for n in result.conflicts} == {"C_VERSION_CONFLICT"}


def test_priority_independent_of_input_order(norm_factory):
    """TRAP-19：打乱输入顺序，分桶与原因码不变（判定是集合运算）。"""
    norms = [
        norm_factory(pk="21", version=1, payload={"status": "A", "amount": "5.00"}),
        norm_factory(pk="21", version=1, payload={"status": "A", "amount": "5.00"}),
        norm_factory(pk="21", version=1, payload={"status": "B", "amount": "5.00"}),
        norm_factory(pk="22", version=2, payload={"status": "A", "amount": "6.00"}),
    ]
    forward = service.classify(list(norms))
    backward = service.classify(list(reversed(norms)))

    def snapshot(result):
        return sorted((n.seq, n.row_class, n.reason_code or "")
                      for n in result.all_rows)

    assert snapshot(forward) == snapshot(backward)
    assert len(forward.conflicts) == 3
    assert len(forward.applied) == 1


def test_quarantine_beats_other_buckets(norm_factory):
    """TRAP-19：两条完全相同的坏行仍是两条隔离，不能合并成一条重复。"""
    bad = {"status": "A", "amount": "N/A", "memo": "x"}
    norms = [norm_factory(pk="31", version=1, payload=bad),
             norm_factory(pk="31", version=1, payload=bad)]
    result = service.classify(norms)
    assert len(result.quarantine) == 2
    assert result.duplicates == []
    assert {n.reason_code for n in result.quarantine} == {"E_UNKNOWN_FIELD"}


def test_signature_uses_normalized_amount(norm_factory):
    """签名用规范化金额：120.5 与 120.50 等价，状态不同则签名不同。"""
    left = norm_factory(pk="41", version=1, payload={"status": "A", "amount": "120.5"})
    right = norm_factory(pk="41", version=1, payload={"status": "A", "amount": "120.50"})
    other = norm_factory(pk="41", version=1, payload={"status": "B", "amount": "120.50"})
    assert dedup.signature(left) == dedup.signature(right)
    assert dedup.signature(left) != dedup.signature(other)
    assert dedup.canonical_payload(left)["amount"] == "120.50"


def test_conflict_group_diff_and_distribution(store, batch_id):
    """冲突分组给出字段差异；分布按码聚合条数。"""
    rows = store.load_events(batch_id, "conflict")
    groups = conflict.conflict_groups_from_rows(rows)
    target = next(g for g in groups if g["key"] == "orders/1005")
    assert target["count"] == 2
    assert "status" in target["diff"]
    rows_all = store.load_events(batch_id)
    dist = service.distribution_from_rows(rows_all)
    mapping = {row["code"]: row["count"] for row in dist}
    assert mapping["C_VERSION_CONFLICT"] == 2
    assert mapping["C_TOMBSTONE_RESURRECT"] == 1
    assert mapping["E_BAD_TIME"] == 1
    assert [row["bucket"] for row in service.rule_table()] == list(service.PRIORITY)
