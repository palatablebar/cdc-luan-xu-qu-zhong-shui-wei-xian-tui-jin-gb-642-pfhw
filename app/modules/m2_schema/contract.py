"""契约层：表清单、字段类型、必填、版本与别名来源。

契约是常量表，不在页面里描述；页面只显示列名与类型，定义写在
docs/DATA-DICTIONARY.md。
"""
from __future__ import annotations

from ... import config

SCHEMAS = {
    "v1": {
        "label": "v1",
        "since": "2026-09-01",
        "note": "旧列名：ref_no / amt / st / qt",
        "emitted_aliases": ("ref_no", "amt", "st", "qt"),
    },
    "v2": {
        "label": "v2",
        "since": "2026-10-15",
        "note": "规范列名：ref / amount / status / qty",
        "emitted_aliases": (),
    },
}

ENVELOPE = ("table", "pk", "version", "op", "event_time", "schema")


def table_known(name: str) -> bool:
    return name in config.TABLES


def table_label(name: str) -> str:
    item = config.TABLES.get(name)
    return item["label"] if item else name


def field_label(name: str) -> str:
    item = config.FIELDS.get(name)
    return item["label"] if item else name


def required_fields(op: str) -> tuple[str, ...]:
    """只有 upsert 需要 status；delete 允许空 payload（墓碑只留键与版本）。"""
    if op == "delete":
        return ()
    return tuple(name for name, meta in config.FIELDS.items() if meta["required"])


def alias_target(name: str) -> str | None:
    return config.ALIASES.get(name)


def is_canonical(name: str) -> bool:
    return name in config.FIELDS


def contract_view() -> dict:
    return {
        "rule_version": config.RULE_VERSION,
        "tables": [
            {"name": name, "label": meta["label"], "pk": meta["pk"]}
            for name, meta in config.TABLES.items()
        ],
        "fields": [
            {"name": name, "type": meta["type"], "required": meta["required"],
             "label": meta["label"],
             "aliases": sorted(k for k, v in config.ALIASES.items() if v == name)}
            for name, meta in config.FIELDS.items()
        ],
        "schemas": [
            {"version": key, "label": meta["label"], "since": meta["since"],
             "note": meta["note"], "emitted": list(meta["emitted_aliases"])}
            for key, meta in SCHEMAS.items()
        ],
        "ops": list(config.OPS),
        "envelope": list(ENVELOPE),
        "money": {"unit": config.MONEY_UNIT, "scale": "0.01", "rounding": "half-up"},
        "window": config.WINDOW_NOTE,
        "late_window_minutes": config.LATE_WINDOW_MINUTES,
    }


def field_rows() -> list[dict]:
    """契约页表格行：字段、类型、必填、别名、说明。"""
    rows = []
    for name, meta in config.FIELDS.items():
        rows.append({
            "name": name, "type": meta["type"], "required": meta["required"],
            "label": meta["label"],
            "aliases": ", ".join(sorted(k for k, v in config.ALIASES.items() if v == name))
                       or "—",
        })
    return rows
