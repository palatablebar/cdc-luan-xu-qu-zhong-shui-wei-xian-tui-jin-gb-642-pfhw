"""冲突判定：同键同版本不同内容、墓碑后的低版本复活。

两条规则都是集合性质，与遍历顺序无关（TRAP-19）：

1. 版本冲突：按 (table, pk, version) 分组，组内出现两种以上签名 → 全组都不应用；
2. 墓碑复活：先算每个键的最大删除版本，版本号更小的 upsert 一律不应用。

先判版本冲突再判墓碑，所以「一个被冲突排除的 delete」不会制造墓碑。
"""
from __future__ import annotations

from ..m2_schema.service import Norm
from . import dedup


def group_key(norm: Norm) -> tuple[str, str, int]:
    return (norm.table, norm.pk, int(norm.version or 0))


def version_conflicts(events: list[Norm]) -> list[Norm]:
    groups: dict[tuple[str, str, int], list[Norm]] = {}
    for norm in events:
        groups.setdefault(group_key(norm), []).append(norm)
    conflicted: list[Norm] = []
    for items in groups.values():
        if len({dedup.signature(i) for i in items}) > 1:
            conflicted.extend(items)
    return sorted(conflicted, key=lambda n: n.seq)


def delete_marks(events: list[Norm]) -> dict[tuple[str, str], tuple[int, int]]:
    """每个键：版本最高的 delete 的 (版本, 到达序号)。

    到达序号用于区分「删除之前就到达的低版本」与「删除之后迟到到达的低版本」：
    前者按版本顺序应用后会被删除覆盖，结果一致；后者试图复活，必须拦下。
    """
    out: dict[tuple[str, str], tuple[int, int]] = {}
    for norm in events:
        if norm.op != "delete" or norm.version is None:
            continue
        key = norm.key()
        current = out.get(key)
        if current is None or norm.version > current[0]:
            out[key] = (norm.version, norm.seq)
    return out


def max_delete_versions(events: list[Norm]) -> dict[tuple[str, str], int]:
    return {key: value[0] for key, value in delete_marks(events).items()}


def tombstone_resurrections(events: list[Norm]) -> list[Norm]:
    marks = delete_marks(events)
    out = []
    for norm in events:
        if norm.op != "upsert" or norm.version is None:
            continue
        mark = marks.get(norm.key())
        if mark is None:
            continue
        delete_version, delete_seq = mark
        if norm.version < delete_version and norm.seq > delete_seq:
            out.append(norm)
    return sorted(out, key=lambda n: n.seq)


def conflict_detail(norm: Norm, peers: list[Norm]) -> str:
    if norm.op == "delete":
        return "删除版本参与冲突"
    others = [p for p in peers if p.seq != norm.seq]
    if not others:
        return ""
    parts = []
    for other in others[:2]:
        parts.append(f"{other.row_ref} 版本 {other.version} 内容不同")
    return "；".join(parts)


def group_peers(events: list[Norm]) -> dict[tuple[str, str, int], list[Norm]]:
    groups: dict[tuple[str, str, int], list[Norm]] = {}
    for norm in events:
        groups.setdefault(group_key(norm), []).append(norm)
    return groups


def conflict_groups(events: list[Norm]) -> list[dict]:
    """冲突分组视图：一行一个 (键, 版本)，列出涉及的行与字段差异。"""
    rows = []
    for key, items in group_peers(events).items():
        sigs = {dedup.signature(i) for i in items}
        if len(sigs) < 2:
            continue
        fields = set()
        for item in items:
            fields.update(item.payload.keys())
        diff = []
        for name in sorted(fields):
            values = {str(item.payload.get(name)) for item in items}
            if len(values) > 1:
                diff.append(f"{name}: " + " / ".join(sorted(values)))
        rows.append({
            "key": f"{key[0]}/{key[1]}", "version": key[2], "count": len(items),
            "refs": [i.row_ref for i in items],
            "diff": "；".join(diff) or "op 或 event_time 不同",
        })
    return sorted(rows, key=lambda r: (r["key"], r["version"]))


def conflict_groups_from_rows(rows: list[dict]) -> list[dict]:
    """已落库冲突行 → 分组视图（一行一个 键+版本）。"""
    groups: dict[tuple[str, str, int], list[dict]] = {}
    for row in rows:
        key = (row["table_name"], row["pk"], int(row["version"] or 0))
        groups.setdefault(key, []).append(row)
    out = []
    for key, items in groups.items():
        payloads = [i.get("payload") or {} for i in items]
        names = sorted({name for payload in payloads for name in payload})
        diff = []
        for name in names:
            values = {str(payload.get(name)) for payload in payloads}
            if len(values) > 1:
                diff.append(f"{name}: " + " / ".join(sorted(values)))
        out.append({
            "key": f"{key[0]}/{key[1]}", "version": key[2], "count": len(items),
            "refs": [i["row_ref"] for i in items],
            "diff": "；".join(diff) or "op 或 event_time 不同",
            "code": items[0].get("reason_code"),
        })
    return sorted(out, key=lambda r: (r["key"], r["version"]))
