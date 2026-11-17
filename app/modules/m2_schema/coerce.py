"""类型归一：全角数字、千分位、空值哨兵、Decimal 量化、UTC 时刻。

这是 TRAP-06 / TRAP-07 / TRAP-10 / TRAP-11 的处理位置：

- 千分位与全角数字先转半角再去分隔符，失败给 E_TYPE_MISMATCH 而不是崩；
- 空值哨兵返回 None（与 0 区分），并记 W_SENTINEL_NULL；
- 金额一律 Decimal 并量化到 0.01（ROUND_HALF_UP），禁止 float 累加；
- 时间先归一 UTC：Z / +08:00 / 无偏移（按 +08:00 解释）三种写法落到同一时刻。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ... import config

FULLWIDTH = str.maketrans("０１２３４５６７８９，．－＋　", "0123456789,.-+ ")


def is_sentinel(text: str) -> bool:
    return text.strip().lower() in config.SENTINELS


def to_decimal(raw) -> tuple[Decimal | None, list[str]]:
    if raw is None:
        return None, []
    if isinstance(raw, bool):
        return None, ["E_TYPE_MISMATCH"]
    if isinstance(raw, Decimal):
        return raw.quantize(config.DECIMAL_QUANT, rounding=ROUND_HALF_UP), []
    if isinstance(raw, (int, float)):
        return (Decimal(str(raw)).quantize(config.DECIMAL_QUANT, rounding=ROUND_HALF_UP), [])
    text = str(raw).strip()
    if is_sentinel(text):
        return None, ["W_SENTINEL_NULL"]
    clean = text.translate(FULLWIDTH).replace(",", "").replace(" ", "")
    try:
        value = Decimal(clean)
    except InvalidOperation:
        return None, ["E_TYPE_MISMATCH"]
    if not value.is_finite():
        return None, ["E_TYPE_MISMATCH"]
    return value.quantize(config.DECIMAL_QUANT, rounding=ROUND_HALF_UP), []


def to_int(raw) -> tuple[int | None, list[str]]:
    if raw is None:
        return None, []
    if isinstance(raw, bool):
        return None, ["E_TYPE_MISMATCH"]
    if isinstance(raw, int):
        return raw, []
    text = str(raw).strip()
    if is_sentinel(text):
        return None, ["W_SENTINEL_NULL"]
    clean = text.translate(FULLWIDTH).replace(",", "").replace(" ", "")
    try:
        return int(clean), []
    except ValueError:
        return None, ["E_TYPE_MISMATCH"]


def parse_time(raw) -> tuple[dt.datetime | None, list[str], str]:
    """返回 (UTC 时刻, 代码, 原始文本)。无偏移时按 +08:00 解释并留提示码。"""
    if raw is None:
        return None, ["E_BAD_TIME"], ""
    text = str(raw).strip()
    if not text or is_sentinel(text):
        return None, ["E_BAD_TIME"], text
    codes: list[str] = []
    body = text
    if body.endswith("Z") or body.endswith("z"):
        body = body[:-1] + "+00:00"
    try:
        value = dt.datetime.fromisoformat(body)
    except ValueError:
        return None, ["E_BAD_TIME"], text
    if value.tzinfo is None:
        value = value.replace(tzinfo=config.CHINA_TZ)
        codes.append("W_TIME_NO_OFFSET")
    return value.astimezone(config.UTC), codes, text


def iso_utc(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(config.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value) -> dt.datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.astimezone(config.UTC)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=config.UTC)
    return parsed.astimezone(config.UTC)


def seconds_between(later: dt.datetime, earlier: dt.datetime) -> int:
    return int((later - earlier).total_seconds())
