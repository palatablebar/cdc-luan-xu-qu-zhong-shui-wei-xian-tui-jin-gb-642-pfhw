"""存储兼容：旧版库（缺 content_digest / canonical_seq / order_basis 列）平滑迁移。

迁移只能加列、不能丢数据，也不能因为新索引引用了尚未补上的列而启动失败。
"""
from __future__ import annotations

import sqlite3

from app import config
from app.store import Store
from app.modules.m6_batches import service

OLD_SCHEMA = """
CREATE TABLE batches(batch_id TEXT PRIMARY KEY,batch_key TEXT,run_id TEXT,source TEXT,
 status TEXT,created_at TEXT,finished_at TEXT,input_digest TEXT,watermark_utc TEXT,
 max_event_time TEXT,params_json TEXT DEFAULT '{}',note TEXT DEFAULT '',replay_of TEXT);
CREATE TABLE events(batch_id TEXT,row_ref TEXT,seq INTEGER,table_name TEXT,pk TEXT,
 version INTEGER,op TEXT,event_time_utc TEXT,event_time_raw TEXT,schema_version TEXT,
 payload_json TEXT DEFAULT '{}',amount TEXT,qty INTEGER,row_class TEXT,reason_code TEXT,
 field TEXT,raw_value TEXT,detail TEXT,late INTEGER DEFAULT 0,flags_json TEXT DEFAULT '[]',
 raw_text TEXT DEFAULT '');
CREATE TABLE watermarks(batch_id TEXT,seq INTEGER,row_ref TEXT,event_time_utc TEXT,
 watermark_utc TEXT,late INTEGER,behind_seconds INTEGER);
CREATE TABLE inputs(batch_id TEXT,seq INTEGER,name TEXT,bytes INTEGER,sha256 TEXT,
 declared_rows INTEGER,encoding TEXT,declared TEXT,kind TEXT,status TEXT,code TEXT,
 note TEXT,lines INTEGER);
CREATE TABLE snapshots(batch_id TEXT,table_name TEXT,pk TEXT,version INTEGER,op TEXT,
 amount TEXT,qty INTEGER,event_time_utc TEXT,row_ref TEXT,late INTEGER,pending INTEGER,
 payload_json TEXT DEFAULT '{}');
CREATE TABLE stages(batch_id TEXT,stage TEXT,in_count INTEGER,out_count INTEGER,
 quarantine_count INTEGER,status TEXT,elapsed_ms INTEGER,note TEXT DEFAULT '');
CREATE TABLE totals(batch_id TEXT,key TEXT,value TEXT);
CREATE TABLE manifests(batch_id TEXT PRIMARY KEY,json TEXT);
"""


def _columns(db_path: str, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_legacy_db_migrates_and_keeps_old_batch(tmp_path):
    db_path = str(tmp_path / "legacy.sqlite")
    conn = sqlite3.connect(db_path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO batches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("B-legacy", "k", "r", "all", "done", "t", None, "dig", None, None,
         "{}", "", None))
    conn.commit()
    conn.close()

    # 新版打开即迁移，不应因新索引引用未补列而失败。
    store = Store(db_path)
    assert "content_digest" in _columns(db_path, "batches")
    assert {"canonical_seq", "order_basis"} <= _columns(db_path, "events")
    assert "canonical_seq" in _columns(db_path, "watermarks")

    old = store.get_batch("B-legacy")
    assert old["status"] == "done"
    assert old["content_digest"] == ""  # 老数据取默认值，不臆造身份

    # 迁移后的库可正常跑新批次并写入规范序，且与旧批次并存不互相覆盖。
    settings = config.Settings(db_path=db_path, sample_dir=config.Settings().sample_dir)
    run = service.run(settings=settings, store=store, source="all")
    new_id = run["batch"]["batch_id"]
    assert new_id != "B-legacy"
    row = store.load_events(new_id)[0]
    assert row["canonical_seq"] >= 1 and row["order_basis"]
    assert {b["batch_id"] for b in store.list_batches()} == {"B-legacy", new_id}

    # 重复打开走 IF NOT EXISTS，不报错、不重复建索引。
    Store(db_path)
