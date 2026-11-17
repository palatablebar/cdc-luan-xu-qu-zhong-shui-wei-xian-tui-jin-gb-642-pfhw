"""契约编排：把一个原始行归一成带证据的规范事件。

归一结果保留：原始行号、原始时间文本、规范 payload、Decimal 金额、告警码与致命码。
致命码（E_*）→ 隔离；告警码（W_*）→ 保留并继续应用。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from ... import config, reasons
from ..m1_events.reader import RawRow
from . import alias, coerce, contract


@dataclass
class Norm:
    row_ref: str
    file: str
    lineno: int
    seq: int
    table: str = ""
    pk: str = ""
    version: int | None = None
    op: str | None = None
    event_time: dt.datetime | None = None
    event_time_raw: str = ""
    schema: str = ""
    payload: dict = field(default_factory=dict)
    amount: Decimal | None = None
    qty: int | None = None
    codes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    bad_field: str = ""
    raw_value: str = ""
    detail: str = ""
    raw_text: str = ""
    late: bool = False
    behind_seconds: int = 0
    row_class: str = ""
    reason_code: str | None = None

    @property
    def time_iso(self) -> str | None:
        return coerce.iso_utc(self.event_time)

    @property
    def all_codes(self) -> list[str]:
        return list(self.errors) + list(self.codes)

    def key(self) -> tuple[str, str]:
        return (self.table, self.pk)

    def to_row(self) -> dict:
        return {
            "row_ref": self.row_ref, "seq": self.seq, "table": self.table,
            "pk": self.pk, "version": self.version, "op": self.op,
            "event_time": self.time_iso, "event_time_raw": self.event_time_raw,
            "schema": self.schema, "payload": self.payload,
            "amount": self.amount, "qty": self.qty,
            "row_class": self.row_class or ("quarantine" if self.errors else "applied"),
            "reason_code": self.reason_code or (self.errors[0] if self.errors else None),
            "field": self.bad_field or None, "raw_value": self.raw_value or None,
            "detail": self.detail or None, "late": self.late,
            "flags": self.codes, "raw_text": self.raw_text[:400],
        }


def _payload_of(raw: dict) -> dict:
    nested = raw.get("payload")
    if isinstance(nested, dict):
        return nested
    return {k: v for k, v in raw.items()
            if k not in contract.ENVELOPE and k != "payload"}


def _fail(norm: Norm, code: str, *, field_name: str = "", raw_value: str = "",
          detail: str = "") -> None:
    if code not in norm.errors:
        norm.errors.append(code)
    if not norm.bad_field and field_name:
        norm.bad_field = field_name
        norm.raw_value = raw_value
    if detail:
        norm.detail = detail if not norm.detail else f"{norm.detail}；{detail}"


def normalize_row(row: RawRow) -> Norm:
    norm = Norm(row_ref=row.row_ref, file=row.file, lineno=row.lineno, seq=row.seq,
                raw_text=row.text)
    if row.error:
        _fail(norm, row.error, detail=row.note or reasons.label(row.error))
        norm.detail = row.note or reasons.label(row.error)
        return norm
    raw = row.raw or {}
    table = str(raw.get("table") or "").strip()
    norm.table = table
    if not contract.table_known(table):
        _fail(norm, "E_BAD_TABLE", field_name="table", raw_value=table)
    pk = "" if raw.get("pk") is None else str(raw.get("pk")).strip()
    norm.pk = pk
    if not pk:
        _fail(norm, "E_MISSING_KEY", field_name="pk", raw_value="")
    version, v_codes = coerce.to_int(raw.get("version"))
    if version is None or version <= 0:
        _fail(norm, "E_BAD_VERSION", field_name="version",
              raw_value=str(raw.get("version")))
    norm.version = version
    op = str(raw.get("op") or "").strip().lower()
    norm.op = op
    if op not in config.OPS:
        _fail(norm, "E_BAD_OP", field_name="op", raw_value=op)
    schema = str(raw.get("schema") or config.DEFAULT_SCHEMA).strip()
    norm.schema = schema
    if schema not in config.SCHEMA_VERSIONS:
        _fail(norm, "E_BAD_SCHEMA", field_name="schema", raw_value=schema)
    when, t_codes, when_raw = coerce.parse_time(raw.get("event_time"))
    norm.event_time_raw = when_raw
    norm.event_time = when
    if when is None:
        _fail(norm, "E_BAD_TIME", field_name="event_time", raw_value=when_raw)
    for code in t_codes:
        # 时间解析返回的错误码已经进 errors，这里只收提示码，避免同一码出现两次
        if code.startswith(("W_", "S_")) and code not in norm.codes:
            norm.codes.append(code)

    payload, p_codes, unknown, ambiguous = alias.normalize_payload(_payload_of(raw), schema)
    norm.payload = payload
    for code in p_codes:
        if code == "E_UNKNOWN_FIELD":
            _fail(norm, code, field_name="payload",
                  raw_value=",".join(unknown), detail="未知列 " + ",".join(unknown))
        elif code == "E_ALIAS_AMBIGUOUS":
            _fail(norm, code, field_name=",".join(ambiguous),
                  raw_value=",".join(ambiguous), detail="新旧列名取值不同")

    amount, a_codes = coerce.to_decimal(payload.get("amount"))
    norm.amount = amount
    if "E_TYPE_MISMATCH" in a_codes:
        _fail(norm, "E_TYPE_MISMATCH", field_name="amount",
              raw_value=str(payload.get("amount")))
    elif "W_SENTINEL_NULL" in a_codes and "W_SENTINEL_NULL" not in norm.codes:
        norm.codes.append("W_SENTINEL_NULL")
    qty, q_codes = coerce.to_int(payload.get("qty"))
    norm.qty = qty
    if "E_TYPE_MISMATCH" in q_codes:
        _fail(norm, "E_TYPE_MISMATCH", field_name="qty", raw_value=str(payload.get("qty")))

    if op == "upsert":
        status = str(payload.get("status") or "").strip()
        if not status:
            _fail(norm, "E_MISSING_FIELD", field_name="status", raw_value=status)
    if not norm.detail:
        labels: list[str] = []
        for code in norm.all_codes:
            label = reasons.label(code)
            if label not in labels:
                labels.append(label)
        norm.detail = "；".join(labels[:3])
    return norm


def normalize_rows(rows: list[RawRow]) -> list[Norm]:
    return [normalize_row(row) for row in rows]


def preview(payload: dict) -> dict:
    """契约页的试归一：把一段原始 JSON 归一后返回代码与规范值。"""
    row = RawRow(file="preview", lineno=1, text="", raw=payload)
    norm = normalize_row(row)
    return {
        "table": norm.table, "pk": norm.pk, "version": norm.version, "op": norm.op,
        "event_time": norm.time_iso, "event_time_raw": norm.event_time_raw,
        "schema": norm.schema, "payload": norm.payload,
        "amount": None if norm.amount is None else str(norm.amount),
        "qty": norm.qty,
        "codes": norm.all_codes,
        "errors": norm.errors,
        "ok": not norm.errors,
        "detail": norm.detail,
        "code_labels": [{"code": c, "label": reasons.label(c),
                         "level": reasons.level_of(c)} for c in norm.all_codes],
    }


def distribution(norms: list[Norm]) -> list[dict]:
    """原因码分布：按码聚合条数，页面与导出共用。"""
    counts: dict[str, int] = {}
    for norm in norms:
        for code in norm.all_codes:
            counts[code] = counts.get(code, 0) + 1
    rows = []
    for code, count in counts.items():
        reason = reasons.get(code)
        rows.append({"code": code, "count": count, "level": reason.level,
                     "stage": reason.stage, "label": reason.title})
    return sorted(rows, key=lambda r: (reasons.LEVEL_ORDER[r["level"]], -r["count"], r["code"]))
