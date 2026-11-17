"""冲突判定：同键同版本不同内容、墓碑后的低版本复活。

两条规则都是集合性质，与遍历/到达顺序无关（TRAP-19）：

1. 版本冲突：按 (table, pk, version) 分组，组内出现两种以上签名 → 全组都不应用；
2. 墓碑复活：某键存在版本 d 的 delete，则该键所有 version < d 的 upsert 一律不应用。
   这是纯版本集合规则，不看谁先到谁后到，因此乱序重发判定恒定。语义上，低于删除
   版本的 upsert 即使物理上先到，最终也会被同键高版本 delete 覆盖，折叠结果一致，
   统一拦下不影响快照，反而让结果可长期复核。

先判版本冲突再判墓碑，所以「一个被冲突排除的 delete」不会制造墓碑。
"""
from __future__ import annotations

from ..m2_schema.service import Norm
from . import dedup


def _order(norm: Norm) -> int:
    return norm.canonical_seq or norm.seq


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
    return sorted(conflicted, key=_order)


def max_delete_versions(events: list[Norm]) -> dict[tuple[str, str], int]:
    """每个键：版本最高的 delete 的版本号。只看版本集合，与到达次序无关。"""
    out: dict[tuple[str, str], int] = {}
    for norm in events:
        if norm.op != "delete" or norm.version is None:
            continue
        key = norm.key()
        if key not in out or norm.version > out[key]:
            out[key] = norm.version
    return out


def tombstone_resurrections(events: list[Norm]) -> list[Norm]:
    """同键存在版本 d 的 delete，则 version < d 的 upsert 一律判复活（顺序无关）。"""
    marks = max_delete_versions(events)
    out = []
    for norm in events:
        if norm.op != "upsert" or norm.version is None:
            continue
        delete_version = marks.get(norm.key())
        if delete_version is None:
            continue
        if norm.version < delete_version:
            out.append(norm)
    return sorted(out, key=_order)


def conflict_detail(norm: Norm, peers: list[Norm]) -> str:
    if norm.op == "delete":
        return "删除版本参与冲突"
    others = [p for p in peers if p.seq != norm.seq]
    if not others:
        return ""
    parts = []
    for other in others[:2]:
        parts.append(f"canonical_seq={_order(other)} 版本 {other.version} 内容不同")
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
