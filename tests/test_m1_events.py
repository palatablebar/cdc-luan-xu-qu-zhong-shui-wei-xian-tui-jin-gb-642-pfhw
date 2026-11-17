"""M1 事件采信：编码回退、BOM、截断、重复表头、列数、空态三态。"""
from __future__ import annotations

import pathlib

import pytest

from app import config
from app.modules.m1_events import decode, format as fmt, reader, service
from fixtures import expected as X

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "sample_data"


def _file(name: str) -> reader.FileRead:
    return reader.read_file(SAMPLES / name, name)


def test_gb18030_fallback_and_mismatch_code():
    """TRAP-01：声明 utf-8、字节是 GB18030，必须回退解码并留原因码。"""
    item = _file(X.GB18030_FILE)
    assert item.encoding == "gb18030"
    assert item.declared == "utf-8"
    assert "E_CHARSET_MISMATCH" in item.codes
    assert item.header[0] == "table"
    assert item.lines == 5


def test_naive_utf8_decode_would_raise():
    """反例：直接按声明编码解码会抛 UnicodeDecodeError，回退链不会。"""
    raw = (SAMPLES / X.GB18030_FILE).read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    assert decode.decode_bytes(raw, declared="utf-8").text.startswith("#")


def test_bom_stripped_before_field_mapping():
    """BOM 出现在文件头：剥离后首个 JSON 行仍然可解析。"""
    body = b"\xef\xbb\xbf" + (
        b'{"table":"orders","pk":"1","version":1,"op":"upsert",'
        b'"event_time":"2026-11-15T08:00:00Z","schema":"v2",'
        b'"payload":{"status":"NEW","amount":"1.00"}}\n')
    item = reader.read_bytes("bom.jsonl", body)
    assert "S_BOM_STRIPPED" in item.codes
    assert item.rows[0].raw["table"] == "orders"
    assert not item.rows[0].text.startswith("\ufeff")


def test_declared_rows_mismatch_marks_truncation():
    """TRAP-16：导出头声明 34 行，实收 33 行 → 判截断。"""
    item = _file(X.MAIN_FILE)
    assert item.declared_rows == X.MAIN_DECLARED_ROWS
    assert item.lines == X.MAIN_ACTUAL_ROWS
    assert "E_TAIL_TRUNCATED" in item.codes
    assert "E_TAIL_TRUNCATED" in item.note or "声明" in item.note


def test_tail_unparsable_line_isolated_as_truncated():
    """TRAP-16：末行只剩半个 JSON，按截断隔离而不是当有效事件。"""
    item = _file(X.MAIN_FILE)
    tail = item.rows[-1]
    assert tail.error == "E_TAIL_TRUNCATED"
    assert tail.raw is None


def test_blank_line_counted_separately():
    """空行单独计数，不计入原始行。"""
    item = _file(X.MAIN_FILE)
    assert item.blank == 1
    assert item.lines == X.MAIN_ACTUAL_ROWS


def test_csv_column_count_mismatch_quarantined():
    """TRAP-05：CSV 行尾多一列 → 严格列数校验并给原值。"""
    item = _file(X.GB18030_FILE)
    bad = [row for row in item.rows if row.error == "E_COLUMN_COUNT"]
    assert len(bad) == 1
    assert "EXTRA" in bad[0].text
    assert bad[0].lineno == 6


def test_csv_repeat_header_removed_and_segmented():
    """分段导出的重复表头被剔除并计段，不当成数据行。"""
    item = _file(X.GB18030_FILE)
    assert "S_HEADER_REPEAT" in item.codes
    assert item.lines == 5
    assert item.declared_rows == 5


def test_empty_header_only_and_all_blank_states(tmp_path):
    """空态三态：0 字节 / 仅表头 / 只有空行，各自给出不同结论。"""
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    header_only = tmp_path / "header_only.csv"
    header_only.write_text("table,pk,version\n", encoding="utf-8")
    blanks = tmp_path / "blanks.jsonl"
    blanks.write_text("\n\n\n", encoding="utf-8")

    codes = {
        reader.read_file(empty, "empty.jsonl").codes[0],
        reader.read_file(header_only, "header_only.csv").codes[0],
        reader.read_file(blanks, "blanks.jsonl").codes[0],
    }
    assert codes == {"S_EMPTY_INPUT", "S_HEADER_ONLY", "E_ALL_BLANK"}


def test_ingest_assigns_arrival_sequence(settings):
    """采信给每行一个到达序号，跨文件连续，重跑时顺序固定。"""
    bundle = service.ingest(settings, source="all")
    assert bundle.counters["files"] == X.FILES
    assert bundle.counters["data_lines"] == X.RAW_LINES
    assert [row.seq for row in bundle.rows] == list(range(1, X.RAW_LINES + 1))
    ctx = fmt.sniff((SAMPLES / X.MAIN_FILE).read_text(encoding="utf-8"))
    assert ctx.kind == "jsonl"
    assert ctx.meta.declared_rows == X.MAIN_DECLARED_ROWS
    assert config.LATE_WINDOW_MINUTES == 10
