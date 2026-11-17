"""M2 契约兼容：别名映射、未知字段、歧义、类型、Decimal、UTC、大整数。"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

from app import config
from app.modules.m1_events.reader import RawRow
from app.modules.m2_schema import alias, coerce, contract, service
from fixtures import expected as X

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "sample_data"


def _norm(**kwargs):
    row = RawRow(file="unit", lineno=1, text="", raw=kwargs)
    row.seq = 1
    return service.normalize_row(row)


def test_v1_alias_maps_to_canonical():
    """TRAP-17：v1 旧列名 ref_no/amt/st 映射到规范列名，不能 KeyError。"""
    norm = _norm(table="orders", pk="1101", version=1, op="upsert",
                 event_time="2026-11-15T08:10:30Z", schema="v1",
                 payload={"ref_no": "1101", "amt": "1,200.50", "st": "已支付"})
    assert norm.payload["ref"] == "1101"
    assert norm.payload["status"] == "已支付"
    assert norm.amount == Decimal("1200.50")
    assert norm.errors == []


def test_alias_ambiguous_quarantined():
    """TRAP-17：新旧列名同时出现且取值不同 → 歧义隔离，不猜哪个对。"""
    norm = _norm(table="customers", pk="2002", version=2, op="upsert",
                 event_time="2026-11-15T08:23:00Z", schema="v2",
                 payload={"status": "新建", "amount": "30.00", "amt": "25.00"})
    assert "E_ALIAS_AMBIGUOUS" in norm.errors


def test_unknown_field_quarantined():
    norm = _norm(table="customers", pk="2001", version=2, op="upsert",
                 event_time="2026-11-15T08:22:00Z", schema="v2",
                 payload={"status": "活跃", "amount": "12.00", "memo": "核对单"})
    assert norm.errors == ["E_UNKNOWN_FIELD"]
    assert norm.bad_field == "payload"
    assert "memo" in norm.raw_value


def test_bigint_pk_kept_as_text():
    """TRAP-09：超过 2^53 的主键按字符串保形，不经过 int() 回写。"""
    raw = ('{"table":"inventory","pk":9007199254740993,"version":1,"op":"upsert",'
           '"event_time":"2026-11-15T08:28:00Z","schema":"v2",'
           '"payload":{"status":"锁定","qty":"7"}}')
    parsed = json.loads(raw)
    row = RawRow(file="unit", lineno=2, text=raw, raw=parsed)
    row.seq = 2
    norm = service.normalize_row(row)
    assert norm.pk == X.BIGINT_PK
    assert norm.pk == str(parsed["pk"])


def test_thousands_and_fullwidth_amount_to_decimal():
    """千分位与全角数字都归一成 Decimal，量化到 0.01。"""
    assert coerce.to_decimal("1,200.50")[0] == Decimal("1200.50")
    assert coerce.to_decimal("１２００．５０")[0] == Decimal("1200.50")
    assert coerce.to_decimal("120.505")[0] == Decimal("120.51")
    assert coerce.to_decimal("abc")[1] == ["E_TYPE_MISMATCH"]


def test_sentinel_amount_is_null_not_zero():
    """哨兵 → None（三态），不是 0；0.00 仍然是真值。"""
    value, codes = coerce.to_decimal("N/A")
    assert value is None
    assert codes == ["W_SENTINEL_NULL"]
    zero, zero_codes = coerce.to_decimal("0.00")
    assert zero == Decimal("0.00")
    assert zero_codes == []
    norm = _norm(table="refunds", pk="7001", version=1, op="upsert",
                 event_time="2026-11-15T08:32:00Z", schema="v2",
                 payload={"status": "待处理", "amount": "N/A"})
    assert norm.amount is None
    assert norm.errors == []
    assert "W_SENTINEL_NULL" in norm.codes


def test_timezone_offsets_collapse_to_same_utc():
    """TRAP-11：08:10Z 与 16:10+08:00 是同一时刻，归一后字符串相同。"""
    left = _norm(table="orders", pk="1004", version=1, op="upsert",
                 event_time="2026-11-15T08:10:00Z", schema="v2",
                 payload={"status": "已支付", "amount": "70.00"})
    right = _norm(table="orders", pk="1004", version=1, op="upsert",
                  event_time="2026-11-15T16:10:00+08:00", schema="v2",
                  payload={"status": "已支付", "amount": "70.00"})
    assert left.time_iso == right.time_iso == "2026-11-15T08:10:00Z"


def test_no_offset_assumes_plus_8_with_code():
    """TRAP-11：没有偏移的时间按 +08:00 解释，并留提示码。"""
    when, codes, raw = coerce.parse_time("2026-11-15T16:40:00")
    assert coerce.iso_utc(when) == "2026-11-15T08:40:00Z"
    assert codes == ["W_TIME_NO_OFFSET"]
    assert raw == "2026-11-15T16:40:00"


def test_bad_time_rejected_with_reason():
    norm = _norm(table="payments", pk="3001", version=1, op="upsert",
                 event_time="2026-11-15T25:10:00+08:00", schema="v2",
                 payload={"status": "已支付", "amount": "18.00"})
    assert "E_BAD_TIME" in norm.errors
    assert norm.event_time is None


def test_contract_snapshot_matches_code():
    """样例里的契约快照与代码常量一致（防止一边改一边忘）。"""
    snapshot = json.loads((SAMPLES / "schema_contract.json").read_text(encoding="utf-8"))
    assert set(snapshot["tables"]) == set(config.TABLES)
    assert snapshot["aliases"] == config.ALIASES
    assert set(snapshot["schemas"]) == set(config.SCHEMA_VERSIONS)
    assert snapshot["window"]["late_minutes"] == config.LATE_WINDOW_MINUTES
    view = contract.contract_view()
    assert [row["name"] for row in view["fields"]] == list(config.FIELDS)
    assert alias.canonical_name("amt") == "amount"
    assert contract.alias_target("amt") == "amount"
    assert contract.required_fields("delete") == ()
