"""采信编排：按来源选文件、逐文件读取、分配到达序号、汇总文件级计数。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ... import config, errors
from . import reader


@dataclass
class IngestBundle:
    source: str
    files: list[reader.FileRead] = field(default_factory=list)
    rows: list[reader.RawRow] = field(default_factory=list)
    counters: dict = field(default_factory=dict)
    codes: list[str] = field(default_factory=list)

    @property
    def digests(self) -> list[dict]:
        return [f.as_input_row() for f in self.files]

    @property
    def input_digest(self) -> str:
        from ... import manifest
        return manifest.digest_parts([f"{f.name}:{f.sha256}" for f in self.files])


def resolve_files(settings, source: str) -> list[str]:
    base = Path(settings.sample_dir)
    if source in ("", "all"):
        names = [n for n in config.SOURCE_ORDER if (base / n).exists()]
    elif source in config.SOURCES:
        names = [n for n in config.SOURCES[source] if (base / n).exists()]
    else:
        candidate = base / source
        if not candidate.exists():
            raise errors.BadRequest(
                f"未知来源 {source!r}；可选 all / " + " / ".join(config.SOURCES)
                + " 或样例目录内的文件名", code="E_BAD_SOURCE")
        names = [source]
    if not names:
        raise errors.BadRequest(f"来源 {source!r} 在样例目录内没有文件", code="E_NO_INPUT")
    return names


def ingest(settings, *, source: str = "all") -> IngestBundle:
    names = resolve_files(settings, source)
    bundle = IngestBundle(source=source)
    base = Path(settings.sample_dir)
    seq = 0
    blank = 0
    header_repeat = 0
    data_lines = 0
    for name in names:
        item = reader.read_file(base / name, name)
        bundle.files.append(item)
        blank += item.blank
        header_repeat += item.codes.count("S_HEADER_REPEAT")
        data_lines += item.lines
        for row in item.rows:
            seq += 1
            row.seq = seq
            bundle.rows.append(row)
        for code in item.codes:
            if code not in bundle.codes:
                bundle.codes.append(code)
    bundle.counters = {
        "files": len(bundle.files),
        "data_lines": data_lines,
        "blank_lines": blank,
        "header_repeat": header_repeat,
        "comments": sum(f.codes.count("S_BOM_STRIPPED") * 0 for f in bundle.files),
        "failed_files": sum(1 for f in bundle.files if f.status != "ok"),
    }
    return bundle


def file_rows(bundle: IngestBundle) -> list[dict]:
    return [f.as_input_row() for f in bundle.files]


def diagnostics(bundle: IngestBundle) -> list[dict]:
    rows = []
    for item in bundle.files:
        rows.append({
            "name": item.name, "kind": item.kind, "encoding": item.encoding,
            "declared": item.declared, "declared_rows": item.declared_rows,
            "lines": item.lines, "blank": item.blank, "bytes": item.size,
            "sha256": item.sha256, "status": item.status, "code": item.code,
            "codes": item.codes, "note": item.note,
        })
    return rows


def log_lines(bundle: IngestBundle) -> list[str]:
    lines = []
    for item in bundle.files:
        lines.append(
            f"[{item.name}] {item.status.upper():<6} 编码={item.encoding or '-'} "
            f"字节={item.size} 行={item.lines}"
            + (f" 码={item.code}" if item.code else "")
            + (f" 备注={item.note}" if item.note else ""))
    return lines
