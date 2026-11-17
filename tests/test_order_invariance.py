"""顺序不变量（本系统的核心裁决）：同一逻辑批次乱序重发，只形成一个可长期复核的结果。

覆盖四条硬保证：

1. 批次身份以规范化业务内容（content_digest）为准，与物理字节、文件名、行号、到达次序、
   空行位置、坏行位置都无关——乱序重发命中同一个 batch_id；
2. 每行带双轨顺序依据：arrival_seq（本次接收次序，仅证据）与 canonical_seq（确定性
   规范序，所有判定的唯一顺序依据）；
3. 分类、迟到、水位、快照逐行业务结果在乱序后完全一致，只有物理证据列（行号引用/接收序）
   允许不同；
4. 同内容重跑走幂等重放，不新增批次、不新增/重复累计行；失败中断保留已完成批次。
"""
from __future__ import annotations

import pathlib
import random
import shutil

import pytest

from app import config, errors
from app.modules.m1_events import identity, service as m1_service
from app.modules.m2_schema import service as m2_service
from app.modules.m6_batches import service as batch_service
from fixtures import expected as X

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "sample_data"
JSONL_FILES = ("events_main.jsonl", "events_late.jsonl")


def _shuffle_jsonl(src: pathlib.Path, dst: pathlib.Path, seed: int) -> None:
    """保留注释头，把其余行（含空行）整体乱序后重写。"""
    lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
    header = [ln for ln in lines if ln.startswith("#")]
    body = [ln for ln in lines if not ln.startswith("#")]
    random.Random(seed).shuffle(body)
    dst.write_text("".join(header + body), encoding="utf-8")


def _shuffled_dir(base: pathlib.Path, seed: int) -> pathlib.Path:
    directory = base / f"shuf_{seed}"
    directory.mkdir()
    for name in JSONL_FILES:
        _shuffle_jsonl(SAMPLES / name, directory / name, seed)
    shutil.copy(SAMPLES / "events_v1_gb.csv", directory / "events_v1_gb.csv")
    return directory


def _run(settings, store):
    return batch_service.run(settings=settings, store=store, source="all")


def _business_signature(store, batch_id: str) -> list[tuple]:
    """剔除物理证据列（行号引用/接收序/顺序依据）后的事件业务指纹。"""
    rows = store.load_events(batch_id)
    return sorted(
        (e["table_name"], e["pk"], e["version"], e["op"], e["row_class"],
         e["reason_code"] or "", e["event_time_utc"], str(e["amount"]),
         1 if e["late"] else 0, e["canonical_seq"], e.get("detail") or "")
        for e in rows)


def _watermark_signature(store, batch_id: str) -> list[tuple]:
    return sorted(
        (w["event_time_utc"], 1 if w["late"] else 0, w["behind_seconds"],
         w["canonical_seq"])
        for w in store.load_watermarks(batch_id))


def _snapshot_signature(store, batch_id: str) -> list[tuple]:
    # row_ref 是物理证据列，不参与业务指纹。
    return sorted(
        (s["table_name"], s["pk"], s["version"], s["op"], str(s["amount"]),
         s["event_time_utc"], 1 if s["late"] else 0, 1 if s["pending"] else 0)
        for s in store.load_snapshot(batch_id))


@pytest.mark.parametrize("seed", (1, 7, 42))
def test_shuffled_resend_is_same_batch_identity(settings, store, tmp_path, seed):
    """乱序重发：content_digest 与 batch_id 恒定，字节摘要允许不同。"""
    first = _run(settings, store)
    shuf_dir = _shuffled_dir(tmp_path, seed)
    shuf_settings = config.Settings(db_path=str(tmp_path / f"s{seed}.sqlite"),
                                    sample_dir=str(shuf_dir))
    shuf_store = type(store)(shuf_settings.db_path)
    second = _run(shuf_settings, shuf_store)

    assert first["batch"]["content_digest"] == second["batch"]["content_digest"]
    assert first["batch"]["batch_id"] == second["batch"]["batch_id"]
    # 物理字节确实变了，证明身份不是靠字节巧合。
    assert first["batch"]["input_digest"] != second["batch"]["input_digest"]


@pytest.mark.parametrize("seed", (1, 7, 42))
def test_shuffled_resend_has_identical_business_result(settings, store, tmp_path, seed):
    """乱序重发：分类/迟到/水位/快照/计数逐行业务结果完全一致。"""
    first = _run(settings, store)
    bid = first["batch"]["batch_id"]
    shuf_dir = _shuffled_dir(tmp_path, seed)
    shuf_settings = config.Settings(db_path=str(tmp_path / f"s{seed}.sqlite"),
                                    sample_dir=str(shuf_dir))
    shuf_store = type(store)(shuf_settings.db_path)
    second = _run(shuf_settings, shuf_store)
    sid = second["batch"]["batch_id"]

    assert _business_signature(store, bid) == _business_signature(shuf_store, sid)
    assert _watermark_signature(store, bid) == _watermark_signature(shuf_store, sid)
    assert _snapshot_signature(store, bid) == _snapshot_signature(shuf_store, sid)
    for key in ("applied", "duplicates", "conflicts", "quarantine",
                "late_events", "on_time_events", "raw_lines", "blank_lines",
                "snapshot_keys", "amount_snapshot", "amount_applied",
                "watermark_utc"):
        assert first["totals"][key] == second["totals"][key]


def test_canonical_and_arrival_seq_both_present(settings, store):
    run = _run(settings, store)
    bid = run["batch"]["batch_id"]
    rows = store.load_events(bid)
    assert len(rows) == X.RAW_LINES
    canonical = [r["canonical_seq"] for r in rows]
    arrival = [r["seq"] for r in rows]
    # 规范序是 1..N 的一个排列；接收序也是 1..N。
    assert sorted(canonical) == list(range(1, X.RAW_LINES + 1))
    assert sorted(arrival) == list(range(1, X.RAW_LINES + 1))
    for r in rows:
        assert r["order_basis"]
        assert "canonical_seq" in r["order_basis"]
        assert "arrival_seq" in r["order_basis"]


def test_canonical_seq_is_content_deterministic(settings, store, tmp_path):
    """同一业务行的 canonical_seq 不随物理位置变化（按业务键核对规范序位次）。"""
    first = _run(settings, store)
    bid = first["batch"]["batch_id"]
    shuf_dir = _shuffled_dir(tmp_path, 77)
    shuf_settings = config.Settings(db_path=str(tmp_path / "det.sqlite"),
                                    sample_dir=str(shuf_dir))
    shuf_store = type(store)(shuf_settings.db_path)
    second = _run(shuf_settings, shuf_store)
    sid = second["batch"]["batch_id"]

    def canon_map(st, b):
        return {(e["table_name"], e["pk"], e["version"], e["op"],
                 e["event_time_utc"], str(e["amount"])): e["canonical_seq"]
                for e in st.load_events(b)}

    assert canon_map(store, bid) == canon_map(shuf_store, sid)


def test_rerun_same_content_is_idempotent_no_accumulation(settings, store):
    first = _run(settings, store)
    bid = first["batch"]["batch_id"]
    before = store.count_rows(bid)
    for _ in range(3):
        replay = _run(settings, store)
        assert replay["idempotent_replay"] is True
        assert replay["batch"]["batch_id"] == bid
        assert store.count_rows(bid) == before
    assert len(store.list_batches()) == 1


def test_failed_attempt_keeps_done_batch_untouched(settings, store):
    first = _run(settings, store)
    bid = first["batch"]["batch_id"]
    before = store.count_rows(bid)
    store.save_attempt(batch={
        "batch_id": "B-failed01", "batch_key": "failed01", "run_id": "CDC-x",
        "source": "all", "status": "failed", "created_at": "2026-11-15T09:00:00Z",
        "input_digest": "bytes", "content_digest": "content", "params": {},
    }, inputs=[])
    # 已完成批次原样保留，失败批次不产生任何结果行。
    assert store.get_batch(bid)["status"] == "done"
    assert store.count_rows(bid) == before
    assert store.count_rows("B-failed01") == {
        "inputs": 0, "events": 0, "snapshots": 0, "watermarks": 0, "stages": 0}


def test_stale_input_rejected_without_touching_done_batch(settings, store, tmp_path):
    first = _run(settings, store)
    bid = first["batch"]["batch_id"]
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "old.jsonl").write_text(
        '{"table":"orders","pk":"7001","version":9,"op":"upsert",'
        '"event_time":"2026-11-15T06:00:00Z","schema":"v2",'
        '"payload":{"status":"新建","amount":"1.00"}}\n',
        encoding="utf-8")
    old_settings = config.Settings(db_path=settings.db_path, sample_dir=str(old_dir))
    with pytest.raises(errors.StaleBatch) as info:
        batch_service.run(settings=old_settings, store=store, source="old.jsonl")
    assert info.value.code == "C_STALE_BATCH"
    assert store.get_batch(bid)["watermark_utc"] == X.WATERMARK


def test_manifest_declares_content_identity_and_order_basis(settings, store):
    run = _run(settings, store)
    doc = store.load_manifest(run["batch"]["batch_id"])
    assert len(doc["content_digest"]) == 64
    assert doc["content_digest"] == run["batch"]["content_digest"]
    assert "content_digest" in doc["identity_basis"]
    assert "canonical_seq" in doc["order_basis"]
    # 字节证据仍保留，便于回查物理文件。
    assert len(doc["input_digest"]) == 64
    assert all(len(i["sha256"]) == 64 for i in doc["inputs"])


def test_bad_line_position_does_not_change_identity(settings, tmp_path):
    """坏 JSON 无论在中段还是末行，内容身份一致（原因码不随行位置漂移）。"""
    good = '{"table":"orders","pk":"1","version":1,"op":"upsert",' \
           '"event_time":"2026-11-15T08:00:00Z","schema":"v2",' \
           '"payload":{"status":"A","amount":"1.00"}}'
    broken = '{"table":"orders","pk":"2","version":1,"op":"upsert","broken'
    d1 = tmp_path / "a"; d1.mkdir()
    (d1 / "f.jsonl").write_text(good + "\n" + broken + "\n", encoding="utf-8")
    d2 = tmp_path / "b"; d2.mkdir()
    (d2 / "f.jsonl").write_text(broken + "\n" + good + "\n", encoding="utf-8")

    def digest(directory):
        s = config.Settings(db_path=str(directory / "db.sqlite"), sample_dir=str(directory))
        bundle = m1_service.ingest(s, source="f.jsonl")
        norms = m2_service.normalize_rows(bundle.rows)
        return identity.digest_of_norms(norms), identity.digest_of_raw_rows(bundle.rows)

    assert digest(d1) == digest(d2)
