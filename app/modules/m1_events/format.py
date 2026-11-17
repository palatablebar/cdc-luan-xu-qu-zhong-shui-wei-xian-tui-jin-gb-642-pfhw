"""格式层：识别 JSONL / CSV、解析导出头、重复表头与列数。

导出头的写法是 `# key=value key=value`（单行），包含 rows（声明条数）与 charset
（声明编码）。声明条数用于 TRAP-16 的完整性校验：实际数据行数与声明不符即判截断。
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

HEADER_META_RE = re.compile(r"([a-zA-Z_]+)=([^\s]+)")


@dataclass
class Meta:
    declared_rows: int | None = None
    declared_charset: str | None = None
    raw: str = ""


@dataclass
class Sniffed:
    kind: str                     # jsonl / csv / empty
    meta: Meta
    comment_lines: int = 0
    header: list[str] = field(default_factory=list)
    header_codes: list[str] = field(default_factory=list)


def parse_meta(line: str) -> Meta:
    body = line.lstrip("#").strip()
    pairs = dict(HEADER_META_RE.findall(body))
    rows: int | None = None
    if pairs.get("rows", "").isdigit():
        rows = int(pairs["rows"])
    return Meta(declared_rows=rows, declared_charset=pairs.get("charset"), raw=body)


def split_comments(text: str) -> tuple[str, Meta, int]:
    """剥掉文件头连续注释行，返回 (剩余文本, 导出头, 注释行数)。"""
    lines = text.splitlines()
    meta = Meta()
    consumed = 0
    for line in lines:
        if line.startswith("#"):
            meta = parse_meta(line)
            consumed += 1
            continue
        break
    rest = "\n".join(lines[consumed:])
    return rest, meta, consumed


def sniff(text: str) -> Sniffed:
    rest, meta, comments = split_comments(text)
    first = next((ln for ln in rest.splitlines() if ln.strip()), "")
    if not first:
        return Sniffed(kind="empty", meta=meta, comment_lines=comments)
    if first.lstrip().startswith("{"):
        return Sniffed(kind="jsonl", meta=meta, comment_lines=comments)
    if "," in first:
        header = next(csv.reader(io.StringIO(first)))
        return Sniffed(kind="csv", meta=meta, comment_lines=comments,
                       header=[h.strip().lstrip("\ufeff") for h in header])
    return Sniffed(kind="jsonl", meta=meta, comment_lines=comments)


def csv_records(text: str) -> tuple[list[str], list[tuple[int, list[str]]], list[str]]:
    """用 csv 模块流式解析，引号内换行不会被切成两条记录。

    返回 (表头, [(行号, 字段列表)], 代码列表)。重复表头被剔除并记 S_HEADER_REPEAT。
    """
    rest, _meta, comments = split_comments(text)
    reader = csv.reader(io.StringIO(rest))
    header: list[str] = []
    rows: list[tuple[int, list[str]]] = []
    codes: list[str] = []
    header_key: list[str] | None = None
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        if not header:
            header = [cell.strip().lstrip("\ufeff") for cell in row]
            header_key = header
            continue
        cells = [cell.strip() for cell in row]
        if header_key is not None and cells == header_key:
            codes.append("S_HEADER_REPEAT")
            continue
        rows.append((reader.line_num + comments, cells))
    return header, rows, codes
