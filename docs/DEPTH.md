# 深度登记 cdcwater（CDC 乱序去重与水位线推进）

- 规模：中型（7 个功能模块 / 7 个页面 / 94 条测试）
- 核心规则零：**内容定身份 + 规范序**——批次键只由规范化业务内容摘要 + 规则版本 + 参数决定，
  与物理字节、文件名、行号、到达次序无关；同一逻辑批次乱序重发命中同一 `batch_id`，逐行结果
  一致、不新增不累计。每行带 `canonical_seq`（判定用规范序）与 `arrival_seq`（接收序，仅证据）。
- 核心规则一：**版本序折叠 + 墓碑**——每个 `(table, pk)` 按 `version` 升序（并列按
  `canonical_seq`）应用，`delete` 写墓碑；同键同版本两种内容整组不应用
  （`C_VERSION_CONFLICT`）；键上存在版本 `d` 的删除，则 `version<d` 的 upsert 一律判
  `C_TOMBSTONE_RESURRECT`，纯版本集合规则、不看到达先后（这些低版本本就被高版本墓碑覆盖，
  快照结果与旧口径一致）。
- 核心规则二：**水位线与半开窗口**——最终水位线 = 本批已应用事件最大 `event_time` − 10 分钟，
  是批次级标量、与接收次序无关；`event_time < 最终水位线` 记迟到但**仍然应用**；快照取
  `event_time ≤ as_of`，待定窗口 `(as_of, +inf)` 半开，两窗口不重叠也不留缝。
- 使用者与工作流：订单库 CDC 管道负责人，每天收 3 个导出文件（主表、旧列名补数、迟到补丁），
  原先靠 grep 日志判断「这条到底应用了没有」；现在一次采信即可得到水位、迟到清单、冲突与
  隔离明细，以及可导出复核的快照与运行清单。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 事件采信 | 选来源跑一次采信，看逐文件诊断与行号 | POST /api/events/import、页面 /events | `app/modules/m1_events/reader.py` `app/modules/m1_events/decode.py` `app/modules/m1_events/format.py` `app/modules/m1_events/service.py` | `tests/test_m1_events.py` |
| M2 契约兼容 | 看字段契约与别名，试归一一条事件 | GET /api/schema、POST /api/schema/preview、页面 /schema | `app/modules/m2_schema/contract.py` `app/modules/m2_schema/coerce.py` `app/modules/m2_schema/alias.py` `app/modules/m2_schema/service.py` | `tests/test_m2_schema.py` |
| M3 去重冲突 | 看重复、冲突、隔离与优先级 | GET /api/conflicts、页面 /conflicts | `app/modules/m3_dedup/dedup.py` `app/modules/m3_dedup/conflict.py` `app/modules/m3_dedup/service.py` | `tests/test_m3_dedup.py` |
| M4 水位迟到 | 看水位时间轴与迟到明细 | GET /api/watermark、页面 / | `app/modules/m4_watermark/watermark.py` `app/modules/m4_watermark/late.py` `app/modules/m4_watermark/service.py` | `tests/test_m4_watermark.py` |
| M5 快照物化 | 按水位物化快照，看待定键与三条对账 | POST /api/snapshot、页面 /snapshot | `app/modules/m5_snapshot/apply.py` `app/modules/m5_snapshot/materialize.py` `app/modules/m5_snapshot/service.py` | `tests/test_m5_snapshot.py` |
| M6 批次续跑 | 查历史、看运行清单、续跑与重跑 | GET /api/batches、POST /api/batches/{id}/resume、页面 /batches | `app/modules/m6_batches/state.py` `app/modules/m6_batches/resume.py` `app/modules/m6_batches/history.py` `app/modules/m6_batches/service.py` | `tests/test_m6_batches.py` |
| M7 审计导出 | 下载证据行、隔离、冲突、快照与清单 | GET /api/export/* | `app/modules/m7_export/csv_out.py` `app/modules/m7_export/json_out.py` `app/modules/m7_export/manifest_out.py` `app/modules/m7_export/service.py` | `tests/test_m7_export.py` |

端到端：`tests/test_e2e.py` 覆盖「样例 → 采信 → 归一 → 去重冲突 → 水位 → 快照 → 导出 → 页面」，
含 4xx 失败输入与幂等重跑；`tests/test_invariants.py` 覆盖行账、键账、金额、幂等、迟到互补。

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧列名导出文件声明 `utf-8`，字节其实是 GB18030 | 按声明解码就崩，或整列乱码 | 声明只作首个候选，失败即回退 gb18030，并记 `E_CHARSET_MISMATCH` | `app/modules/m1_events/decode.py` | `tests/test_m1_events.py::test_gb18030_fallback_and_mismatch_code` |
| TRAP-05 | CSV 行尾多一个分隔符（1102 行多出 EXTRA） | 静默丢弃多余列或错位 | 严格列数校验，整行隔离并保留原值 | `app/modules/m1_events/reader.py` | `tests/test_m1_events.py::test_csv_column_count_mismatch_quarantined` |
| TRAP-09 | 主键 `9007199254740993` 超过 2^53，另有前导零 `00123` | `int()` 化后丢精度或丢零 | 主键全程字符串；JSON 导出超过 2^53 的整数也转字符串 | `app/modules/m7_export/json_out.py`、`app/modules/m2_schema/service.py` | `tests/test_m7_export.py::test_csv_keeps_bigint_and_leading_zero`、`tests/test_m2_schema.py::test_bigint_pk_kept_as_text` |
| TRAP-11 | 同一时刻写成 `08:10Z` 与 `16:10+08:00`，另有无偏移时间 | 落进两个时刻、去重失效 | 先归一 UTC；无偏移按 +08:00 解释并记 `W_TIME_NO_OFFSET` | `app/modules/m2_schema/coerce.py` | `tests/test_m2_schema.py::test_timezone_offsets_collapse_to_same_utc`、`tests/test_m3_dedup.py::test_timezone_written_duplicate_merged` |
| TRAP-13 | 补数文件整批比当前水位早（主文件 08:40 那条落后最大事件 22 分钟） | 水位一推进就丢迟到事件 | 迟到窗口 10 分钟，以最终水位线判迟到但仍应用，快照标 `late`；标尺是批次级标量，与接收次序无关 | `app/modules/m4_watermark/watermark.py` | `tests/test_m4_watermark.py::test_late_event_still_applied`、`tests/test_m4_watermark.py::test_late_judged_against_final_watermark` |
| TRAP-14 | 同键同版本两种内容（1005 的 PAID / CANCELLED），删除后飘来低版本（1012 的 v2） | 后写覆盖，或整批丢弃，看不出保留了哪条 | 两条都进冲突表、都不应用，并给出证据（规范序位次）与差异字段；墓碑复活按纯版本集合规则判定 | `app/modules/m3_dedup/conflict.py` | `tests/test_m3_dedup.py::test_same_version_two_payloads_both_conflict`、`tests/test_m3_dedup.py::test_tombstone_rule_independent_of_arrival_order` |
| TRAP-16 | 主文件声明 34 行实际 33 行，末行只剩半个 JSON | 半行被当有效事件，凭空多一条 | 文件级声明条数校验记 `E_TAIL_TRUNCATED`；行级解析失败统一 `E_BAD_JSON`，与该行处于中段/末行无关，避免原因码随行位置漂移 | `app/modules/m1_events/reader.py` | `tests/test_m1_events.py::test_declared_rows_mismatch_marks_truncation`、`tests/test_m1_events.py::test_tail_unparsable_line_isolated_as_bad_json` |
| TRAP-17 | v1 改列名（`ref_no`/`amt`/`st`）后新旧混发 | `KeyError` 或整行丢弃 | 别名映射到规范列名；新旧同时出现且取值不同判 `E_ALIAS_AMBIGUOUS` | `app/modules/m2_schema/alias.py` | `tests/test_m2_schema.py::test_v1_alias_maps_to_canonical`、`tests/test_m2_schema.py::test_alias_ambiguous_quarantined` |
| TRAP-19 | 一条事件同时像「重复」又像「冲突」，坏行还互相重复 | 按代码书写顺序覆盖，结果随遍历顺序变 | 优先级 `quarantine > conflict > duplicate > apply`，各桶由集合运算得出 | `app/modules/m3_dedup/service.py` | `tests/test_m3_dedup.py::test_conflict_beats_duplicate`、`tests/test_m3_dedup.py::test_priority_independent_of_input_order` |
| TRAP-23 | 事件 `event_time` 恰好等于水位线 / 恰好等于 `as_of` | 两端都算，重复计入或漏计 | 迟到用半开 `[水位线, +inf)`；快照含 `as_of`、待定窗口 `(as_of, +inf)` 半开 | `app/modules/m4_watermark/watermark.py`、`app/modules/m5_snapshot/materialize.py` | `tests/test_m4_watermark.py::test_boundary_equal_watermark_is_not_late`、`tests/test_m5_snapshot.py::test_boundary_event_at_as_of_included_once` |
| TRAP-26 | 拿上一轮旧导出再跑一次（最大事件 06:00 < 已持久化水位 08:52） | 无条件覆盖，水位回退、快照变旧 | 陈旧批次直接 409 `C_STALE_BATCH`，已落库结果不动 | `app/modules/m6_batches/resume.py`、`app/modules/m6_batches/state.py` | `tests/test_m6_batches.py::test_stale_input_rejected`、`tests/test_order_invariance.py::test_stale_input_rejected_without_touching_done_batch` |
| TRAP-27 | 上游把同一逻辑批次乱序重发，文件字节/行序变了但业务事实没变 | 按字节摘要生成新批次，水位、迟到数随行序漂移，页面与导出出现两套结果 | 批次身份改用规范化业务内容 `content_digest`（与字节/行号/到达序无关），所有判定改走 `canonical_seq` 规范序；迟到以批次最终水位为唯一标尺、墓碑复活用纯版本规则；同内容重放幂等不累计 | `app/modules/m1_events/identity.py`、`app/modules/m6_batches/state.py`、`app/modules/m4_watermark/watermark.py` | `tests/test_order_invariance.py`（整文件） |

## 对账规则

- **行账**：原始数据行 = 已应用 + 重复 + 冲突 + 隔离（空行与注释行单独计数）；
- **键账**：已应用键 = 快照键 + 只有待定事件的键；快照键 = 活跃键 + 墓碑键；
- **金额账**：快照金额 + 待定金额 + 变化金额 = 已应用金额（Decimal，单位元，2 位）；
- **迟到账**：迟到 + 准时 = 已应用（迟到只打标记，不改变应用结果）；
- 四条账在首页、`/snapshot` 与 `manifest.json` 的 `reconcile` 中同时出现，任一条不平会显式为「不平」。

样例数据的四账：行 42 = 24 + 2 + 6 + 10；键 19 = 18 + 1；快照 18 = 16 活跃 + 2 墓碑；
金额 1552.51 + 200.00 + 0.00 = 1752.51。迟到 22 + 准时 2 = 已应用 24（最终水位 08:52 标尺）。
冲突 6 = 版本冲突 2 + 墓碑复活 4；被复活拦下的低版本本就被同键高版本墓碑覆盖，
故快照键数与金额与按到达序口径完全相同。

## 反例与不变量

- 反例（朴素实现会挂）：
  - `tests/test_m1_events.py::test_naive_utf8_decode_would_raise`（按声明编码直接解码抛
    `UnicodeDecodeError`，回退链不抛）；
  - `tests/test_m3_dedup.py::test_conflict_beats_duplicate`（顺序覆盖会把它判成 1 冲突 + 1 重复）；
  - `tests/test_m3_dedup.py::test_tombstone_rule_independent_of_arrival_order`（按到达序区分
    「删除前/后」会让同一批事实在乱序重发时得到不同复活判定；纯版本规则下这些低版本都被同键
    高版本墓碑覆盖，快照金额不变，见 `tests/test_order_invariance.py`）；
  - `tests/test_order_invariance.py::test_shuffled_resend_is_same_batch_identity`（按字节/到达序
    定身份会给同一批业务事实生成两个 batch_id、两套水位迟到；内容定身份后恒定）；
  - `tests/test_m6_batches.py::test_stale_input_rejected`（后写覆盖会让水位从 08:52 退回 06:00）。
- 不变量：
  - `tests/test_invariants.py::test_row_accounting_identity`（行账守恒）；
  - `tests/test_invariants.py::test_key_accounting_identity`（键账守恒）；
  - `tests/test_invariants.py::test_amount_conservation_is_exact`（金额守恒，Decimal 精确）；
  - `tests/test_invariants.py::test_idempotent_rerun_keeps_rows_and_totals`（幂等重跑）；
  - `tests/test_m4_watermark.py::test_watermark_monotonic`（水位单调）；
  - `tests/test_m5_snapshot.py::test_rebuild_is_idempotent`（物化可重复）。

## 已知不覆盖

- 只处理文件型导出，不接 Kafka / Debezium 在线流；水位线是「批内推进」，不是全局流进度。
- 不做跨批次键级合并：每个批次独立折叠，快照只反映当前批次；跨批次的复活检测需要把历史
  状态一起纳入，属于下一期。
- 事务边界未建模：同一事务的多条变更不保证原子可见。
- 中文数字、货币符号（`￥`）不在金额归一范围内，遇到直接按类型错误隔离。
