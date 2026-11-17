"""样例数据的期望值：按事件逐条推算后写死，与实现无关。

推算依据（docs/DATA-DICTIONARY.md 的定义）：

- 原始行 = 已应用 27 + 重复 2 + 冲突 3 + 隔离 10 = 42；
- 水位线 = 最大 event_time(09:02) − 10 分钟 = 08:52；
- 键 19 = 快照键 18 + 待定键 1，快照键 18 = 活跃 16 + 墓碑 2；
- 金额：快照 1552.51 + 待定 200.00 + 变化 0.00 = 已应用 1752.51（元）。
"""

# ---- 行账 ----
FILES = 3
RAW_LINES = 42
BLANK_LINES = 1
HEADER_REPEAT = 1
APPLIED = 27
DUPLICATES = 2
CONFLICTS = 3
QUARANTINE = 10

# ---- 水位 ----
MAX_EVENT_TIME = "2026-11-15T09:02:00Z"
WATERMARK = "2026-11-15T08:52:00Z"
AS_OF = WATERMARK
LATE_EVENTS = 9
ON_TIME_EVENTS = 18
LATE_12_MIN_REF = "events_main.jsonl:30"      # 08:40 到达，落后最大事件 720 秒
LATE_12_MIN_SECONDS = 720
BOUNDARY_REF = "events_main.jsonl:29"         # 08:52 恰等于 as_of，归快照一侧

# ---- 键账 ----
KEYS_TOTAL = 19
SNAPSHOT_KEYS = 18
ACTIVE_KEYS = 16
TOMBSTONE_KEYS = 2
PENDING_ONLY_KEYS = 1
PENDING_EVENTS = 1
DRIFT_KEYS = 0
TOMBSTONE_LIST = (("orders", "1001"), ("orders", "1012"))
PENDING_KEY = ("orders", "1008")

# ---- 金额账（Decimal 字符串，单位元）----
AMOUNT_SNAPSHOT = "1552.51"
AMOUNT_PENDING = "200.00"
AMOUNT_DRIFT = "0.00"
AMOUNT_APPLIED = "1752.51"

# ---- 主键保形 ----
BIGINT_PK = "9007199254740993"                 # 超过 2^53
LEADING_ZERO_PK = "00123"                      # 前导零不能丢
SENTINEL_AMOUNT_KEY = ("refunds", "7001")      # 金额是 N/A 哨兵

# ---- 冲突与隔离的具体行 ----
VERSION_CONFLICT_REFS = ("events_main.jsonl:10", "events_main.jsonl:11")
RESURRECT_REF = "events_main.jsonl:33"
DUP_REFS = ("events_main.jsonl:5", "events_main.jsonl:9")
QUARANTINE_CODES = {
    "events_main.jsonl:14": "E_UNKNOWN_FIELD",
    "events_main.jsonl:15": "E_ALIAS_AMBIGUOUS",
    "events_main.jsonl:16": "E_BAD_TIME",
    "events_main.jsonl:17": "E_BAD_OP",
    "events_main.jsonl:18": "E_BAD_TABLE",
    "events_main.jsonl:19": "E_MISSING_KEY",
    "events_main.jsonl:23": "E_TYPE_MISMATCH",
    "events_main.jsonl:31": "E_BAD_JSON",
    "events_main.jsonl:35": "E_TAIL_TRUNCATED",
    "events_v1_gb.csv:6": "E_COLUMN_COUNT",
}

# ---- 文件级诊断 ----
GB18030_FILE = "events_v1_gb.csv"
MAIN_FILE = "events_main.jsonl"
MAIN_DECLARED_ROWS = 34
MAIN_ACTUAL_ROWS = 33

# ---- 干净样例（同一条代码路径，无脏行）----
CLEAN_SOURCE = "events_clean.jsonl"
CLEAN_APPLIED = 3
CLEAN_LATE = 0
CLEAN_SNAPSHOT_KEYS = 2
CLEAN_AS_OF = "2026-11-15T08:40:00Z"
