"""CSV 导出：证据行、隔离区、冲突表、快照。

约定：UTF-8 带 BOM（Excel 直接打开不乱码）；金额列写 Decimal 字符串，主键列写
字符串，前导零与超过 2^53 的整数都按原始字符输出（TRAP-09）。
"""
from __future__ import annotations

import csv
import io

BOM = "\ufeff"

EVENT_COLUMNS = ("行号引用", "表", "主键", "版本", "操作", "事件时间(UTC)",
                 "原始时间", "schema", "分类", "原因码", "金额(元)", "数量",
                 "迟到", "明细")
QUARANTINE_COLUMNS = ("行号引用", "表", "主键", "字段", "原值", "原因码", "明细",
                      "原始文本")
CONFLICT_COLUMNS = ("行号引用", "键", "版本", "操作", "事件时间(UTC)", "原因码", "明细")
SNAPSHOT_COLUMNS = ("表", "主键", "版本", "状态", "金额(元)", "数量",
                    "事件时间(UTC)", "行号引用", "迟到", "待定")


def to_csv(columns: tuple[str, ...], rows: list[list]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if cell is None else cell for cell in row])
    return buffer.getvalue()


def encode_csv(text: str) -> bytes:
    return (BOM + text).encode("utf-8")


def event_rows(rows: list[dict]) -> list[list]:
    out = []
    for row in rows:
        out.append([
            row["row_ref"], row["table_name"], row["pk"], row["version"], row["op"],
            row["event_time_utc"], row.get("event_time_raw"), row.get("schema_version"),
            row["row_class"], row.get("reason_code") or "",
            "" if row.get("amount") is None else str(row["amount"]),
            row.get("qty"), "是" if row.get("late") else "否",
            row.get("detail") or "",
        ])
    return out


def quarantine_rows(rows: list[dict]) -> list[list]:
    out = []
    for row in rows:
        out.append([
            row["row_ref"], row["table_name"], row["pk"], row.get("field") or "",
            row.get("raw_value") or "", row.get("reason_code") or "",
            row.get("detail") or "", row.get("raw_text") or "",
        ])
    return out


def conflict_rows(rows: list[dict]) -> list[list]:
    out = []
    for row in rows:
        out.append([
            row["row_ref"], f"{row['table_name']}/{row['pk']}", row["version"],
            row["op"], row["event_time_utc"], row.get("reason_code") or "",
            row.get("detail") or "",
        ])
    return out


def snapshot_rows(rows: list[dict]) -> list[list]:
    out = []
    for row in rows:
        out.append([
            row["table_name"], row["pk"], row["version"],
            "墓碑" if row["op"] == "delete" else "活跃",
            "" if row.get("amount") is None else str(row["amount"]),
            row.get("qty"), row["event_time_utc"], row["row_ref"],
            "是" if row.get("late") else "否", "是" if row.get("pending") else "否",
        ])
    return out
