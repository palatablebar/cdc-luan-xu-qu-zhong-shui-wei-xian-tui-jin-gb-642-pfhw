# cdcwater · CDC 乱序去重与水位线推进

订单库变更流（CDC）每天 120 万条事件，8 张表。下游拿到的是补数、重投、乱序混在一起的
导出文件：同一版本可能来两次、可能带不同内容、删除之后还会飘来一条旧更新。这个工具把
一批导出采信进来，按版本折叠成「每个键一个状态」，并给出水位线、迟到条数、冲突与隔离
清单，全部可导出复核。

## 能力

- 采信 JSONL 与 CSV 两种导出格式，保留 `文件名:行号` 与原始文本；
- 编码回退（utf-8 → gb18030），声明与实际不符会留原因码；重复表头、列数不齐、尾行截断各自判定；
- v1/v2 列名演进按别名归一，契约外字段隔离，新旧列名同时出现判歧义；
- 完全重复按「规范化全行签名」去重，时区写法不同但同一时刻也合并且注明；
- 同键同版本不同内容判 `C_VERSION_CONFLICT`，墓碑之后的低版本 upsert 按纯版本集合规则判
  `C_TOMBSTONE_RESURRECT`（不看到达先后），两条都不应用；
- 水位线 = 本批已应用事件最大 `event_time` − 10 分钟，迟到以**最终水位线**为唯一标尺、与
  接收次序无关，迟到**仍然应用**；
- 快照按 `event_time ≤ as_of` 物化，`as_of` 之后的事件留待定窗口，一并给出合计；
- **批次身份以规范化业务内容（`content_digest`）为准**，与物理字节、文件名、行号、到达次序
  无关：同一逻辑批次乱序重发只形成一个 `batch_id`，逐行结果一致、不新增不累计；每行带
  `canonical_seq`（判定用规范序）与 `arrival_seq`（接收序，仅证据）双轨顺序依据；
- 导出事件证据、隔离区、冲突表、快照 CSV/JSON 与运行清单（含 `run_id`、内容摘要与每个输入的
  sha256）。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 |
|---|---|---|---|
| M1 事件采信 | 选来源跑一次采信，看到逐文件诊断 | `POST /api/events/import`、页面 `/events` | `reader.py` `decode.py` `format.py` |
| M2 契约兼容 | 看列契约与别名，试归一一条事件 | `GET /api/schema`、`POST /api/schema/preview`、页面 `/schema` | `contract.py` `coerce.py` `alias.py` |
| M3 去重冲突 | 看重复、冲突、隔离与原因码分布 | `GET /api/conflicts`、页面 `/conflicts` | `dedup.py` `conflict.py` |
| M4 水位迟到 | 看水位时间轴与迟到明细 | `GET /api/watermark`、页面 `/` | `watermark.py` `late.py` |
| M5 快照物化 | 按水位物化快照，看待定键 | `POST /api/snapshot`、页面 `/snapshot` | `apply.py` `materialize.py` |
| M6 批次续跑 | 查历史、看清单、续跑与重跑 | `GET /api/batches`、页面 `/batches` | `state.py` `resume.py` `history.py` |
| M7 审计导出 | 下载证据行、隔离、冲突、快照与清单 | `GET /api/export/*` | `csv_out.py` `json_out.py` `manifest_out.py` |

模块内统一是 `handler.py`（入口）+ `service.py`（编排）+ 两个纯规则/读写文件；
定义写在 `docs/DATA-DICTIONARY.md`，坑与测试映射写在 `docs/DEPTH.md`。

## 核心规则

1. **内容定身份**：`batch_key` 只由规范化业务内容摘要 + 规则版本 + 参数决定，乱序重发同一批
   事实命中同一 `batch_id`；物理字节 sha256 仅作证据；
2. **版本序应用**：每个 `(table, pk)` 按 `version` 升序（并列按 `canonical_seq`）折叠，
   `delete` 写墓碑；
3. **冲突不猜**：同键同版本两种内容 → 整组不应用；键上存在版本 `d` 的删除，则 `version<d`
   的 upsert 一律不复活（纯版本规则，与到达次序无关）；
4. **水位单调、顺序无关**：最终水位 = `max(event_time) − 10min`，迟到只与最终水位比较，
   重复/冲突/隔离不推进，迟到只标记不丢弃；
5. **快照半开**：`[-, as_of]` 进快照、`(as_of, +inf)` 待定，两窗口不重叠；
6. **金额 Decimal**：`0.01` 量化、`ROUND_HALF_UP`，合计可独立复算；
7. **失败可续、重跑幂等**：失败只写 failed 尝试、保留旧版结果；同内容重放走幂等重放，
   不新增批次、不新增或重复累计行。

## 启动

```bash
docker compose up --build          # 服务在 http://127.0.0.1:18201
docker compose exec -T app python -m pytest -q
docker compose down -v             # 连数据库卷一起删
```

首次启动会用自带样例跑一次采信，页面直接有水位刻度、事件流、冲突与快照；
再点「采信」或「重跑」命中同一批次键，只复用结果、不新增行。

## 演示步骤

1. 首页看水位时间轴：竖线是水位线，黄色刻度是迟到；
2. `/events` 按分类筛选，看每行的行号引用与原因码；
3. `/conflicts` 看冲突分组、隔离区与优先级表，导出隔离 CSV；
4. `/snapshot` 点「物化」，看活跃 / 墓碑 / 待定与三条对账；
5. `/batches` 看 `run_id`、输入 sha256、阶段耗时，导出运行清单。

## API 与页面

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/health` | 健康检查与水位数 |
| POST | `/api/events/import` | 采信（`source=all/main/v1_csv/late`） |
| GET | `/api/events` | 事件流（可按分类、表过滤） |
| GET | `/api/schema` | 契约、别名、版本 |
| POST | `/api/schema/preview` | 单条试归一 |
| GET | `/api/conflicts` | 冲突、隔离、重复、分布 |
| GET | `/api/watermark` | 水位、刻度、迟到表 |
| GET | `/api/snapshot` / POST `/api/snapshot` | 读快照 / 重新物化 |
| GET | `/api/batches`、`/api/batches/{id}`、`/api/batches/{id}/manifest` | 批次与清单 |
| POST | `/api/batches/{id}/resume` | 续跑 |
| GET | `/api/export/{events,quarantine,conflicts,snapshot}.csv` | 证据导出 |
| GET | `/api/export/{snapshot,manifest}.json` | 快照与运行清单 |

页面共 6 个：`/`、`/events`、`/schema`、`/conflicts`、`/snapshot`、`/batches`。

## 目录

```text
app/
  main.py config.py errors.py reasons.py manifest.py store.py web.py bootstrap.py
  modules/m1_events/ … m7_export/        # 每模块 handler + service + 2 个规则文件
  templates/                             # base + 6 页
  static/                                # theme / layout / components + 3 个 js
docs/  DEPTH.md  DATA-DICTIONARY.md  RUNBOOK.md
sample_data/  events_main.jsonl  events_v1_gb.csv  events_late.jsonl  events_clean.jsonl  schema_contract.json
tests/  test_m1_events.py … test_m7_export.py  test_e2e.py  test_invariants.py  fixtures/
```

## 已知不覆盖

- 只处理文件型导出，不连接 Kafka / Debezium 这类在线流；
- schema 演进只覆盖列改名与新增字段，不支持类型收窄；
- 同一批次内不做跨批次键级合并（每个批次独立折叠，快照只反映当前批次）；
- 事件级事务边界（同一事务的多个变更原子可见）未建模。
