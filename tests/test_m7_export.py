"""M7 审计导出：BOM、证据列、大整数保形、Decimal 字符串、清单内容。"""
from __future__ import annotations

import json
from decimal import Decimal

from app.modules.m7_export import csv_out, json_out, manifest_out, service
from fixtures import expected as X


def _text(data: bytes) -> str:
    return data.decode("utf-8-sig")


def test_events_csv_has_bom_and_evidence_columns(store, batch_id):
    data, name = service.render_csv(store, batch_id, "events")
    assert data.startswith(b"\xef\xbb\xbf")
    assert name.endswith("-events.csv")
    text = _text(data)
    header = text.splitlines()[0]
    for column in ("行号引用", "原因码", "金额(元)", "分类", "迟到"):
        assert column in header
    assert len(text.splitlines()) == X.RAW_LINES + 1


def test_csv_keeps_bigint_and_leading_zero(store, batch_id):
    """TRAP-09：主键列按字符串输出，2^53 以上与前导零都不变形。"""
    text = _text(service.render_csv(store, batch_id, "events")[0])
    assert X.BIGINT_PK in text
    assert X.LEADING_ZERO_PK in text
    snapshot = _text(service.render_csv(store, batch_id, "snapshot")[0])
    assert X.BIGINT_PK in snapshot
    assert ",00123," in snapshot


def test_amount_keeps_two_decimals(store, batch_id):
    text = _text(service.render_csv(store, batch_id, "snapshot")[0])
    assert "1200.50" in text
    assert "1200.5," not in text
    row = next(line for line in text.splitlines() if line.startswith("orders,1101"))
    cells = row.split(",")
    assert cells[4] == "1200.50"


def test_snapshot_json_amounts_are_strings(store, batch_id):
    """TRAP-09：JSON 里金额是字符串，不会在 JS 侧掉精度。"""
    data, name = service.render_snapshot_json(store, batch_id)
    payload = json.loads(data.decode("utf-8"))
    assert name.endswith("-snapshot.json")
    assert payload["as_of_utc"] == X.AS_OF
    assert payload["amounts"]["snapshot"] == X.AMOUNT_SNAPSHOT
    for row in payload["rows"]:
        assert row["amount"] is None or isinstance(row["amount"], str)
        assert isinstance(row["pk"], str)
    big = next(row for row in payload["rows"] if row["pk"] == X.BIGINT_PK)
    assert big["pk"] == X.BIGINT_PK


def test_json_keeps_bigint_as_text():
    payload = {"pk": 9007199254740993, "small": 42, "amount": Decimal("12.30")}
    text = json_out.dumps(payload)
    assert '"pk": "9007199254740993"' in text
    assert '"small": 42' in text
    assert '"amount": "12.30"' in text


def test_manifest_json_has_inputs_and_watermark(store, batch_id):
    data, name = service.render_manifest(store, batch_id)
    payload = json.loads(data.decode("utf-8"))
    assert name.endswith("-manifest.json")
    assert payload["run_id"].startswith("CDC-")
    assert payload["watermark_utc"] == X.WATERMARK
    assert [item["name"] for item in payload["inputs"]] == ["events_main.jsonl",
                                                           "events_v1_gb.csv",
                                                           "events_late.jsonl"]
    assert len(payload["input_digest"]) == 64
    assert "counters" in payload and "reconcile" in payload


def test_quarantine_csv_has_reason_and_raw_value(store, batch_id):
    text = _text(service.render_csv(store, batch_id, "quarantine")[0])
    lines = text.splitlines()
    assert len(lines) == X.QUARANTINE + 1
    assert "E_UNKNOWN_FIELD" in text
    assert "memo" in text
    assert "events_main.jsonl:35" in text


def test_conflict_csv_lists_evidence_refs(store, batch_id):
    text = _text(service.render_csv(store, batch_id, "conflicts")[0])
    lines = text.splitlines()
    assert len(lines) == X.CONFLICTS + 1
    for ref in X.VERSION_CONFLICT_REFS + (X.RESURRECT_REF,):
        assert ref in text
    assert "C_TOMBSTONE_RESURRECT" in text


def test_dataset_catalog_counts(store, batch_id):
    rows = {row["kind"]: row for row in service.catalog(store, batch_id)}
    assert rows["events"]["rows"] == X.RAW_LINES
    assert rows["quarantine"]["rows"] == X.QUARANTINE
    assert rows["conflicts"]["rows"] == X.CONFLICTS
    assert rows["snapshot"]["rows"] == X.SNAPSHOT_KEYS
    assert rows["manifest"]["rows"] == 1
    assert rows["events"]["columns"] == len(csv_out.EVENT_COLUMNS)
    summaries = service.summaries(store, batch_id)
    assert summaries["batch_id"] == batch_id
    assert summaries["empty_hint"] is None
    assert manifest_out.digest_of({"a": 1}) == manifest_out.digest_of({"a": 1})
