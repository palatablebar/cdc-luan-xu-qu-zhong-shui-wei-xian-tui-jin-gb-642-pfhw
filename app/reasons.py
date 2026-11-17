"""原因码唯一来源：跨模块共享，页面/导出/测试都从这里取文案。

一个原因码 = 级别 + 所属阶段 + 中文结论 + 复核建议。级别三档：
info（可忽略的采集记录）、warn（需人工看一眼）、error（已隔离/冲突，不进快照）。
"""
from __future__ import annotations

from typing import NamedTuple

INFO = "info"
WARN = "warn"
ERROR = "error"
LEVEL_ORDER = {ERROR: 0, WARN: 1, INFO: 2}
LEVEL_LABELS = {ERROR: "隔离", WARN: "提示", INFO: "记录"}


class Reason(NamedTuple):
    code: str
    level: str
    stage: str
    title: str
    hint: str


def _r(code: str, level: str, stage: str, title: str, hint: str) -> Reason:
    return Reason(code, level, stage, title, hint)


REASONS = {
    # ---- M1 采信：字节、行、截断 ----
    "E_DECODE_FAILED": _r(
        "E_DECODE_FAILED", ERROR, "ingest",
        "回退链内所有编码都无法解码该文件", "确认导出端编码后补进回退链"),
    "E_CHARSET_MISMATCH": _r(
        "E_CHARSET_MISMATCH", WARN, "ingest",
        "文件声明编码与实际字节不符，已按回退链解码", "让导出端改回声明编码"),
    "S_BOM_STRIPPED": _r(
        "S_BOM_STRIPPED", INFO, "ingest",
        "文件带 UTF-8 BOM，已剥离", "导出端可关掉 BOM"),
    "S_HEADER_REPEAT": _r(
        "S_HEADER_REPEAT", INFO, "ingest",
        "分段导出出现重复表头，已剔除", "分段导出可改成单段"),
    "E_TAIL_TRUNCATED": _r(
        "E_TAIL_TRUNCATED", ERROR, "ingest",
        "末行不完整或行数与声明不符，末行已丢弃", "重新导出完整文件后重跑"),
    "E_BAD_JSON": _r(
        "E_BAD_JSON", ERROR, "ingest",
        "行内 JSON 无法解析", "多为写入中断，回源补齐该行"),
    "S_EMPTY_INPUT": _r(
        "S_EMPTY_INPUT", INFO, "ingest",
        "文件 0 行", "确认导出任务是否真的没有变更"),
    "S_HEADER_ONLY": _r(
        "S_HEADER_ONLY", INFO, "ingest",
        "文件只有表头，没有数据行", "同上，等下一次导出"),
    "E_ALL_BLANK": _r(
        "E_ALL_BLANK", ERROR, "ingest",
        "文件只有空行", "导出端写坏了，重新导出"),
    # ---- M2 契约：必填、类型、别名、时区 ----
    "E_BAD_TABLE": _r(
        "E_BAD_TABLE", ERROR, "contract",
        "表名不在登记清单内", "新表先登记再采信"),
    "E_MISSING_KEY": _r(
        "E_MISSING_KEY", ERROR, "contract",
        "缺少 table / pk 等必需信封字段", "补主键后重放该事件"),
    "E_BAD_VERSION": _r(
        "E_BAD_VERSION", ERROR, "contract",
        "version 不是正整数", "版本号决定应用顺序，缺失不能猜"),
    "E_BAD_OP": _r(
        "E_BAD_OP", ERROR, "contract",
        "op 不是 upsert / delete", "新增操作类型需先扩展应用规则"),
    "E_BAD_TIME": _r(
        "E_BAD_TIME", ERROR, "contract",
        "event_time 无法解析为时刻", "检查是否混入 25 点、斜杠日期或空值"),
    "E_BAD_SCHEMA": _r(
        "E_BAD_SCHEMA", ERROR, "contract",
        "schema 版本未登记", "先补别名映射再采信"),
    "E_UNKNOWN_FIELD": _r(
        "E_UNKNOWN_FIELD", ERROR, "contract",
        "payload 出现契约外字段", "确认是新增字段还是拼写错误"),
    "E_ALIAS_AMBIGUOUS": _r(
        "E_ALIAS_AMBIGUOUS", ERROR, "contract",
        "新旧列名同时出现且取值不同", "只保留一列，重放后再采信"),
    "E_MISSING_FIELD": _r(
        "E_MISSING_FIELD", ERROR, "contract",
        "upsert 缺少必填字段 status", "补状态后重放"),
    "E_TYPE_MISMATCH": _r(
        "E_TYPE_MISMATCH", ERROR, "contract",
        "字段类型不符（金额/数量）", "按契约改成数字，不要塞说明文字"),
    "E_COLUMN_COUNT": _r(
        "E_COLUMN_COUNT", ERROR, "contract",
        "列数与表头不一致", "检查行尾多余分隔符或截断"),
    "W_SENTINEL_NULL": _r(
        "W_SENTINEL_NULL", WARN, "contract",
        "金额是空值哨兵，按空处理不计入合计", "哨兵不是 0，确认是否漏填"),
    "W_TIME_NO_OFFSET": _r(
        "W_TIME_NO_OFFSET", WARN, "contract",
        "event_time 没有时区偏移，按 +08:00 解释", "导出端补上偏移"),
    # ---- M3 去重与冲突 ----
    "D_EXACT_DUP": _r(
        "D_EXACT_DUP", WARN, "dedup",
        "完全相同的事件重复到达，保留首条", "上游重复投递，可忽略"),
    "D_TZ_MERGED": _r(
        "D_TZ_MERGED", WARN, "dedup",
        "时区写法不同但同一时刻、内容相同，已合并", "统一用 Z 输出可避免重复"),
    "C_VERSION_CONFLICT": _r(
        "C_VERSION_CONFLICT", ERROR, "dedup",
        "同一键同一版本出现不同内容", "两条都不应用，回源确认哪条为准"),
    "C_TOMBSTONE_RESURRECT": _r(
        "C_TOMBSTONE_RESURRECT", ERROR, "dedup",
        "墓碑之后的低版本更新试图复活该键", "版本号必须大于删除版本才允许复活"),
    # ---- M4 水位与迟到 ----
    "W_LATE_ARRIVAL": _r(
        "W_LATE_ARRIVAL", WARN, "watermark",
        "event_time 早于水位线，迟到但仍应用", "已进快照，核对是否漏掉补数窗口"),
    "W_WATERMARK_REGRESS": _r(
        "W_WATERMARK_REGRESS", WARN, "watermark",
        "本次最大 event_time 低于已持久化水位，水位不回退", "确认是否重放了旧文件"),
    # ---- M5 快照 ----
    "W_PENDING_WINDOW": _r(
        "W_PENDING_WINDOW", WARN, "snapshot",
        "事件落在水位之后，本次快照不含该键", "等下一轮水位推过后再物化"),
    "S_TOMBSTONE": _r(
        "S_TOMBSTONE", INFO, "snapshot",
        "该键最新版本是删除，快照留墓碑", "墓碑参与去重，不出现在活跃行"),
    # ---- M6 批次 ----
    "B_IDEMPOTENT_REPLAY": _r(
        "B_IDEMPOTENT_REPLAY", INFO, "batches",
        "同一输入摘要命中已完成批次，直接复用结果", "重跑幂等：不新增行、不改合计"),
    "B_RESUMED": _r(
        "B_RESUMED", INFO, "batches",
        "从断点续跑，已采信文件不再重复解码", "续跑沿用原水位"),
    "B_NO_STATE": _r(
        "B_NO_STATE", WARN, "batches",
        "该批次没有可用的断点状态，按全新批次重跑", "失败过早时会遇到"),
    "C_STALE_BATCH": _r(
        "C_STALE_BATCH", ERROR, "batches",
        "输入最大 event_time 低于已持久化水位，按陈旧批次拒绝", "确认导出端时钟与文件版本"),
    # ---- M7 导出 ----
    "X_EMPTY_SELECTION": _r(
        "X_EMPTY_SELECTION", WARN, "export",
        "导出选择为空", "先跑一次采信再导出"),
}


def get(code: str) -> Reason:
    return REASONS.get(code, Reason(code, WARN, "unknown", code, "未登记的原因码"))


def level_of(code: str) -> str:
    return get(code).level


def label(code: str) -> str:
    return get(code).title


def stage_of(code: str) -> str:
    return get(code).stage


def is_error(code: str) -> bool:
    return level_of(code) == ERROR


def catalog() -> list[dict]:
    rows = sorted(REASONS.values(), key=lambda r: (r.stage, LEVEL_ORDER[r.level], r.code))
    return [
        {"code": r.code, "level": r.level, "stage": r.stage, "title": r.title,
         "hint": r.hint, "level_label": LEVEL_LABELS[r.level]}
        for r in rows
    ]
