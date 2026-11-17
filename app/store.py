"""SQLite 存储：批次、输入文件、事件、快照、水位序列、阶段、合计、运行清单。

约定：

- 金额列一律 TEXT（Decimal 字符串），不落 float；主键 pk 一律 TEXT，前导零与
  超过 2^53 的整数都按原样保存；
- 一次运行的所有行在同一个事务里「先删后插」，失败整体回滚，不留半批数据；
- 页面永远只展示一个批次，所有查询都带 batch_id。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    batch_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    input_digest TEXT NOT NULL,
    watermark_utc TEXT,
    max_event_time TEXT,
    params_json TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT '',
    replay_of TEXT
);
CREATE INDEX IF NOT EXISTS idx_batches_key ON batches(batch_key, status);
CREATE TABLE IF NOT EXISTS inputs (
    batch_id TEXT NOT NULL, seq INTEGER NOT NULL, name TEXT NOT NULL,
    bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, declared_rows INTEGER,
    encoding TEXT, declared TEXT, kind TEXT, status TEXT NOT NULL, code TEXT,
    note TEXT, lines INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    batch_id TEXT NOT NULL, row_ref TEXT NOT NULL, seq INTEGER NOT NULL,
    table_name TEXT NOT NULL, pk TEXT NOT NULL, version INTEGER,
    op TEXT, event_time_utc TEXT, event_time_raw TEXT, schema_version TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}', amount TEXT, qty INTEGER,
    row_class TEXT NOT NULL, reason_code TEXT, field TEXT, raw_value TEXT,
    detail TEXT, late INTEGER NOT NULL DEFAULT 0, flags_json TEXT NOT NULL DEFAULT '[]',
    raw_text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_class ON events(batch_id, row_class);
CREATE TABLE IF NOT EXISTS snapshots (
    batch_id TEXT NOT NULL, table_name TEXT NOT NULL, pk TEXT NOT NULL,
    version INTEGER NOT NULL, op TEXT NOT NULL, amount TEXT, qty INTEGER,
    event_time_utc TEXT NOT NULL, row_ref TEXT NOT NULL,
    late INTEGER NOT NULL DEFAULT 0, pending INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS watermarks (
    batch_id TEXT NOT NULL, seq INTEGER NOT NULL, row_ref TEXT NOT NULL,
    event_time_utc TEXT NOT NULL, watermark_utc TEXT NOT NULL,
    late INTEGER NOT NULL DEFAULT 0, behind_seconds INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS stages (
    batch_id TEXT NOT NULL, stage TEXT NOT NULL, in_count INTEGER NOT NULL,
    out_count INTEGER NOT NULL, quarantine_count INTEGER NOT NULL,
    status TEXT NOT NULL, elapsed_ms INTEGER NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS totals (
    batch_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manifests (
    batch_id TEXT PRIMARY KEY, json TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).resolve().parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_columns(conn)

    @staticmethod
    def _ensure_columns(conn) -> None:
        """轻量迁移：老库缺列时补上，避免升级后 500。"""
        wanted = {"inputs": {"declared": "TEXT"}}
        for table, columns in wanted.items():
            existing = {row["name"] for row in conn.execute(
                f"PRAGMA table_info({table})").fetchall()}
            for column, ddl in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------- 批次 ----------
    def get_batch(self, batch_id: str) -> dict | None:
        rows = self._all("SELECT * FROM batches WHERE batch_id=?", (batch_id,))
        return rows[0] if rows else None

    def find_done_by_key(self, batch_key: str) -> dict | None:
        rows = self._all(
            "SELECT * FROM batches WHERE batch_key=? AND status='done' "
            "ORDER BY created_at LIMIT 1", (batch_key,))
        return rows[0] if rows else None

    def latest_batch(self, status: str = "done") -> dict | None:
        rows = self._all(
            "SELECT * FROM batches WHERE status=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (status,))
        if not rows:
            rows = self._all("SELECT * FROM batches ORDER BY created_at DESC, rowid DESC LIMIT 1",
                             ())
        return rows[0] if rows else None

    def list_batches(self, limit: int = 30) -> list[dict]:
        return self._all(
            "SELECT * FROM batches ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))

    def watermark_high(self) -> str | None:
        rows = self._all(
            "SELECT watermark_utc FROM batches WHERE status='done' "
            "AND watermark_utc IS NOT NULL ORDER BY watermark_utc DESC LIMIT 1", ())
        return rows[0]["watermark_utc"] if rows else None

    # ---------- 一次运行落库（单事务替换） ----------
    def save_run(self, *, batch: dict, inputs: list[dict], events: list[dict],
                 snapshots: list[dict], watermarks: list[dict], stages: list[dict],
                 totals: dict, manifest: dict) -> None:
        with self.connect() as conn:
            for table in ("inputs", "events", "snapshots", "watermarks", "stages", "totals"):
                conn.execute(f"DELETE FROM {table} WHERE batch_id=?", (batch["batch_id"],))
            for i, item in enumerate(inputs, 1):
                conn.execute(
                    """INSERT INTO inputs (batch_id,seq,name,bytes,sha256,declared_rows,
                        encoding,declared,kind,status,code,note,lines)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (batch["batch_id"], i, item["name"], item["bytes"], item["sha256"],
                     item.get("declared_rows"), item.get("encoding"),
                     item.get("declared"), item.get("kind"), item["status"],
                     item.get("code"), item.get("note", ""), item.get("lines", 0)))
            for row in events:
                conn.execute(
                    """INSERT INTO events (batch_id,row_ref,seq,table_name,pk,version,op,
                        event_time_utc,event_time_raw,schema_version,payload_json,amount,qty,
                        row_class,reason_code,field,raw_value,detail,late,flags_json,raw_text)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (batch["batch_id"], row["row_ref"], row["seq"], row["table"], row["pk"],
                     row.get("version"), row.get("op"), row.get("event_time"),
                     row.get("event_time_raw"), row.get("schema"),
                     json.dumps(row.get("payload", {}), ensure_ascii=False,
                                default=str),
                     None if row.get("amount") is None else str(row["amount"]),
                     row.get("qty"), row["row_class"], row.get("reason_code"),
                     row.get("field"), row.get("raw_value"), row.get("detail"),
                     1 if row.get("late") else 0,
                     json.dumps(row.get("flags", []), ensure_ascii=False),
                     row.get("raw_text", "")))
            for s in snapshots:
                conn.execute(
                    """INSERT INTO snapshots (batch_id,table_name,pk,version,op,amount,qty,
                        event_time_utc,row_ref,late,pending,payload_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (batch["batch_id"], s["table"], s["pk"], s["version"], s["op"],
                     None if s.get("amount") is None else str(s["amount"]), s.get("qty"),
                     s["event_time"], s["row_ref"], 1 if s.get("late") else 0,
                     1 if s.get("pending") else 0,
                     json.dumps(s.get("payload", {}), ensure_ascii=False, default=str)))
            for w in watermarks:
                conn.execute(
                    """INSERT INTO watermarks (batch_id,seq,row_ref,event_time_utc,
                        watermark_utc,late,behind_seconds) VALUES (?,?,?,?,?,?,?)""",
                    (batch["batch_id"], w["seq"], w["row_ref"], w["event_time"],
                     w["watermark"], 1 if w.get("late") else 0, w.get("behind_seconds", 0)))
            for s in stages:
                conn.execute(
                    """INSERT INTO stages (batch_id,stage,in_count,out_count,
                        quarantine_count,status,elapsed_ms,note) VALUES (?,?,?,?,?,?,?,?)""",
                    (batch["batch_id"], s["stage"], s["in_count"], s["out_count"],
                     s["quarantine_count"], s["status"], s["elapsed_ms"], s.get("note", "")))
            for key, value in totals.items():
                conn.execute("INSERT INTO totals (batch_id,key,value) VALUES (?,?,?)",
                             (batch["batch_id"], key, str(value)))
            conn.execute(
                "INSERT INTO manifests (batch_id,json) VALUES (?,?) "
                "ON CONFLICT(batch_id) DO UPDATE SET json=excluded.json",
                (batch["batch_id"], json.dumps(manifest, ensure_ascii=False, default=str)))
            conn.execute(
                """INSERT INTO batches (batch_id,batch_key,run_id,source,status,created_at,
                        finished_at,input_digest,watermark_utc,max_event_time,params_json,
                        note,replay_of)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(batch_id) DO UPDATE SET
                     status=excluded.status, finished_at=excluded.finished_at,
                     note=excluded.note, input_digest=excluded.input_digest,
                     watermark_utc=excluded.watermark_utc,
                     max_event_time=excluded.max_event_time""",
                (batch["batch_id"], batch["batch_key"], batch["run_id"], batch["source"],
                 batch["status"], batch["created_at"], batch.get("finished_at"),
                 batch["input_digest"], batch.get("watermark_utc"),
                 batch.get("max_event_time"),
                 json.dumps(batch.get("params", {}), ensure_ascii=False),
                 batch.get("note", ""), batch.get("replay_of")))


    # ---------- 快照（可重复物化） ----------
    def replace_snapshot(self, batch_id: str, rows: list[dict], totals: dict) -> None:
        """按同一 as_of 重算快照：先删后插，不新增事件行。"""
        with self.connect() as conn:
            conn.execute("DELETE FROM snapshots WHERE batch_id=?", (batch_id,))
            for s in rows:
                conn.execute(
                    """INSERT INTO snapshots (batch_id,table_name,pk,version,op,amount,qty,
                        event_time_utc,row_ref,late,pending,payload_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (batch_id, s["table"], s["pk"], s["version"], s["op"],
                     None if s.get("amount") is None else str(s["amount"]), s.get("qty"),
                     s["event_time"], s["row_ref"], 1 if s.get("late") else 0,
                     1 if s.get("pending") else 0,
                     json.dumps(s.get("payload", {}), ensure_ascii=False, default=str)))
            for key, value in totals.items():
                conn.execute("DELETE FROM totals WHERE batch_id=? AND key=?",
                             (batch_id, key))
                conn.execute("INSERT INTO totals (batch_id,key,value) VALUES (?,?,?)",
                             (batch_id, key, str(value)))

    def save_attempt(self, *, batch: dict, inputs: list[dict], note: str = "") -> None:
        """失败尝试：只写批次与输入，不动任何已存在批次的结果行。"""
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO batches (batch_id,batch_key,run_id,source,status,created_at,
                        finished_at,input_digest,watermark_utc,max_event_time,params_json,
                        note,replay_of)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(batch_id) DO UPDATE SET
                     status=excluded.status, finished_at=excluded.finished_at,
                     note=excluded.note""",
                (batch["batch_id"], batch["batch_key"], batch["run_id"], batch["source"],
                 batch["status"], batch["created_at"], batch.get("finished_at"),
                 batch["input_digest"], batch.get("watermark_utc"),
                 batch.get("max_event_time"),
                 json.dumps(batch.get("params", {}), ensure_ascii=False),
                 note or batch.get("note", ""), batch.get("replay_of")))
            conn.execute("DELETE FROM inputs WHERE batch_id=?", (batch["batch_id"],))
            for i, item in enumerate(inputs, 1):
                conn.execute(
                    """INSERT INTO inputs (batch_id,seq,name,bytes,sha256,declared_rows,
                        encoding,declared,kind,status,code,note,lines)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (batch["batch_id"], i, item["name"], item["bytes"], item["sha256"],
                     item.get("declared_rows"), item.get("encoding"),
                     item.get("declared"), item.get("kind"), item["status"],
                     item.get("code"), item.get("note", ""), item.get("lines", 0)))

    # ---------- 读取 ----------
    def load_inputs(self, batch_id: str) -> list[dict]:
        return self._all("SELECT * FROM inputs WHERE batch_id=? ORDER BY seq", (batch_id,))

    def load_events(self, batch_id: str, row_class: str | None = None) -> list[dict]:
        if row_class:
            rows = self._all(
                "SELECT * FROM events WHERE batch_id=? AND row_class=? ORDER BY seq",
                (batch_id, row_class))
        else:
            rows = self._all("SELECT * FROM events WHERE batch_id=? ORDER BY seq", (batch_id,))
        for r in rows:
            r["payload"] = json.loads(r.pop("payload_json") or "{}")
            r["flags"] = json.loads(r.pop("flags_json") or "[]")
            r["amount"] = None if r["amount"] is None else Decimal(r["amount"])
            r["late"] = bool(r["late"])
        return rows

    def load_snapshot(self, batch_id: str) -> list[dict]:
        rows = self._all(
            "SELECT * FROM snapshots WHERE batch_id=? ORDER BY table_name, pk", (batch_id,))
        for r in rows:
            r["payload"] = json.loads(r.pop("payload_json") or "{}")
            r["amount"] = None if r["amount"] is None else Decimal(r["amount"])
            r["late"] = bool(r["late"])
            r["pending"] = bool(r["pending"])
        return rows

    def load_watermarks(self, batch_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM watermarks WHERE batch_id=? ORDER BY seq", (batch_id,))

    def load_stages(self, batch_id: str) -> list[dict]:
        from . import config
        rows = self._all("SELECT * FROM stages WHERE batch_id=? ORDER BY rowid", (batch_id,))
        for row in rows:
            row["label"] = config.STAGE_LABELS.get(row["stage"], row["stage"])
        return rows

    def load_totals(self, batch_id: str) -> dict:
        rows = self._all("SELECT key, value FROM totals WHERE batch_id=?", (batch_id,))
        return {r["key"]: r["value"] for r in rows}

    def load_manifest(self, batch_id: str) -> dict | None:
        rows = self._all("SELECT json FROM manifests WHERE batch_id=?", (batch_id,))
        return json.loads(rows[0]["json"]) if rows else None

    def count_rows(self, batch_id: str) -> dict:
        out = {}
        for table in ("inputs", "events", "snapshots", "watermarks", "stages"):
            out[table] = self._all(
                f"SELECT COUNT(*) AS n FROM {table} WHERE batch_id=?", (batch_id,))[0]["n"]
        return out

    def _all(self, sql: str, args: tuple) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
