"""列名归一：v1 旧列名映射到规范列名，未知列与歧义列分别判定。

TRAP-17：v1 改列名后，读入侧必须按别名映射，不能 KeyError 或整行丢弃；
同时不允许「新旧列名同时出现却取值不同」这种二义被静默接受（TRAP-19 的
同类规则：判定靠集合运算，与遍历顺序无关）。
"""
from __future__ import annotations

from ... import config


def canonical_name(name: str) -> str:
    return config.ALIASES.get(name, name)


def normalize_payload(payload: dict, schema: str = config.DEFAULT_SCHEMA
                      ) -> tuple[dict, list[str], list[str], list[str]]:
    """返回 (规范 payload, 代码列表, 未知列, 歧义列)。"""
    out: dict = {}
    unknown: list[str] = []
    ambiguous: list[str] = []
    codes: list[str] = []
    for key, value in payload.items():
        name = str(key).strip()
        target = canonical_name(name)
        if target not in config.FIELDS:
            unknown.append(name)
            continue
        if target in out:
            if not _same(out[target], value):
                ambiguous.append(target)
            continue
        out[target] = value
    if unknown:
        codes.append("E_UNKNOWN_FIELD")
    if ambiguous:
        codes.append("E_ALIAS_AMBIGUOUS")
    return out, codes, sorted(unknown), sorted(ambiguous)


def _same(left, right) -> bool:
    return str(left).strip() == str(right).strip()


def alias_rows() -> list[dict]:
    """契约页的别名表：旧列名 → 规范列名 → 来源版本。"""
    rows = []
    for alias, target in config.ALIASES.items():
        rows.append({"alias": alias, "target": target,
                     "origin": config.ALIAS_ORIGIN.get(alias, "—"),
                     "target_label": config.FIELDS[target]["label"]})
    return sorted(rows, key=lambda r: r["alias"])
