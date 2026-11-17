"""cdcwater 全局配置：迟到窗口、编码链、表清单、字段契约与样例来源。

规则唯一来源，其他模块只读不写：

- 事件时间入库一律 UTC（ISO8601 带 Z），页面按 Asia/Shanghai(+08:00) 显示；
- 金额一律 Decimal，单位「元」，最小刻度 0.01，禁止 float 累加；
- 水位线 = 已应用事件的最大 event_time − 迟到窗口；
- 迟到判定为半开区间：event_time < 水位线 记迟到，等于水位线不算迟到；
- 快照窗口：含等号一侧 [.., as_of]，待定窗口为半开 (as_of, +inf)，两窗口不重叠。
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHINA_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
UTC = dt.timezone.utc

RULE_VERSION = "cdcwater/1.0.0"
DECIMAL_QUANT = Decimal("0.01")
MONEY_UNIT = "元"

# 迟到窗口：水位线 = 已应用事件最大 event_time − 该窗口
LATE_WINDOW_MINUTES = 10

STAGES = ("ingest", "contract", "dedup", "watermark", "snapshot", "export")
STAGE_LABELS = {
    "ingest": "采信",
    "contract": "契约",
    "dedup": "去重冲突",
    "watermark": "水位迟到",
    "snapshot": "快照",
    "export": "导出",
}
STAGE_FLOW = "采信 → 契约 → 去重冲突 → 水位迟到 → 快照 → 导出"

# 编码回退链：先按声明尝试，再按严格顺序逐个试；gb18030 是 gbk/gb2312 的超集
ENCODING_CHAIN = ("utf-8", "gb18030")
CHARSET_ALIASES = {
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "gb18030": "gb18030",
    "utf8": "utf-8",
    "utf-8": "utf-8",
    "utf-8-sig": "utf-8",
}

# 空值哨兵：集中定义；哨兵 → None（三态：哨兵 / 0 真值 / 缺列）
SENTINELS = frozenset(
    {"", "-", "--", "—", "——", "/", "n/a", "na", "null", "none", "nil",
     "未知", "无", "暂无", "待定", "不适用", "未提供"}
)

OPS = ("upsert", "delete")
SCHEMA_VERSIONS = ("v1", "v2")
DEFAULT_SCHEMA = "v2"

# 8 张登记表：不在清单内的表名一律隔离，不猜
TABLES = {
    "orders": {"label": "订单", "pk": "订单号"},
    "order_items": {"label": "订单行", "pk": "行号"},
    "customers": {"label": "客户", "pk": "客户号"},
    "payments": {"label": "支付", "pk": "支付号"},
    "shipments": {"label": "发货", "pk": "运单号"},
    "products": {"label": "商品", "pk": "商品号"},
    "inventory": {"label": "库存", "pk": "库存键"},
    "refunds": {"label": "退款", "pk": "退款号"},
}

# 规范化后的 payload 字段契约（类型 / 是否必填 / 中文名）
FIELDS = {
    "ref": {"type": "string", "required": False, "label": "业务引用"},
    "status": {"type": "string", "required": True, "label": "状态"},
    "amount": {"type": "decimal", "required": False, "label": "金额(元)"},
    "qty": {"type": "int", "required": False, "label": "数量"},
}

# v1 旧列名 → 规范化列名；v2 也接受旧名（读兼容），重名同时出现即歧义
ALIASES = {
    "ref_no": "ref",
    "amt": "amount",
    "st": "status",
    "qt": "qty",
}
ALIAS_ORIGIN = {
    "ref_no": "v1",
    "amt": "v1",
    "st": "v1",
    "qt": "v1",
}

# 样例来源：文件名固定，顺序即采信顺序（同一顺序保证结果可复算）
SOURCES = {
    "main": ("events_main.jsonl",),
    "v1_csv": ("events_v1_gb.csv",),
    "late": ("events_late.jsonl",),
}
SOURCE_ORDER = ("events_main.jsonl", "events_v1_gb.csv", "events_late.jsonl")
SOURCE_LABELS = {
    "all": "全部来源（3 个文件）",
    "main": "主文件 events_main.jsonl",
    "v1_csv": "旧列名 events_v1_gb.csv",
    "late": "迟到补丁 events_late.jsonl",
}

CSV_COLUMNS = ("table", "pk", "version", "op", "event_time", "schema",
               "ref_no", "amt", "st", "qt")

# 半开区间的书面表达，页面与导出共用同一句
WINDOW_NOTE = "快照窗口含 as_of；待定窗口为 (as_of, +inf)"


@dataclass(frozen=True)
class Settings:
    """运行参数；测试用 replace() 覆盖，不依赖环境变量分支。"""

    db_path: str = str(PROJECT_ROOT / "var" / "cdcwater.sqlite")
    sample_dir: str = str(PROJECT_ROOT / "sample_data")
    rule_version: str = RULE_VERSION
    late_window_minutes: int = LATE_WINDOW_MINUTES
    seed_on_start: bool = True
    export_dir: str = str(PROJECT_ROOT / "var" / "export")

    @classmethod
    def from_env(cls) -> "Settings":
        base = cls()
        db_path = os.getenv("CDCWATER_DB") or base.db_path
        sample_dir = os.getenv("CDCWATER_SAMPLE_DIR") or base.sample_dir
        window = os.getenv("CDCWATER_LATE_WINDOW") or ""
        minutes = base.late_window_minutes
        if window.strip():
            minutes = int(window)
        seed = os.getenv("CDCWATER_SEED")
        seed_on_start = base.seed_on_start if seed is None else seed.strip() not in ("0", "false", "no")
        return replace(cls(), db_path=db_path, sample_dir=sample_dir,
                       late_window_minutes=minutes, seed_on_start=seed_on_start)

    @property
    def late_window(self) -> dt.timedelta:
        return dt.timedelta(minutes=self.late_window_minutes)

    @property
    def data_dir(self) -> Path:
        return Path(self.db_path).resolve().parent
