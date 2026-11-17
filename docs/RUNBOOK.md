# 运行手册 cdcwater

## 1 启动与自检

```bash
docker compose config --quiet
docker compose up -d --build
curl -s http://127.0.0.1:18201/api/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18201/
docker compose exec -T app python -m pytest -q
```

首次启动会用 `sample_data/` 的三个文件跑一次采信（`app/bootstrap.py`），页面直接有数据；
重复启动或同一逻辑批次乱序重发都命中同一内容批次键，走幂等重放，不新增行、不累计。

## 2 主流程（页面）

1. `/` 水位时间轴 → 选来源 → 点「采信」；
2. `/events` 按分类筛选，确认每行的行号引用与原因码；
3. `/conflicts` 看冲突分组与隔离区，导出隔离 CSV；
4. `/snapshot` 点「物化」，核对三条对账；
5. `/batches` 看 `run_id`、内容摘要（批次身份）、输入字节 sha256（证据）与阶段耗时，导出运行清单；
   事件流每行可看 `canonical_seq`（判定用规范序）与接收序（仅证据）。

## 3 主流程（命令）

```bash
BASE=http://127.0.0.1:18201
curl -s -X POST $BASE/api/events/import -H 'content-type: application/json' -d '{"source":"all"}' | head -c 600
curl -s "$BASE/api/conflicts" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["counters"])'
curl -s "$BASE/api/watermark"  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["watermark"])'
curl -s -X POST $BASE/api/snapshot | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["rows"], d["amounts"])'
curl -s -o /tmp/events.csv      "$BASE/api/export/events.csv"
curl -s -o /tmp/quarantine.csv  "$BASE/api/export/quarantine.csv"
curl -s -o /tmp/conflicts.csv   "$BASE/api/export/conflicts.csv"
curl -s -o /tmp/snapshot.csv    "$BASE/api/export/snapshot.csv"
curl -s -o /tmp/manifest.json   "$BASE/api/export/manifest.json"
```

判定看数字：行账 42 = 24 + 2 + 6 + 10；键账 19 = 18 + 1；金额 1552.51 + 200.00 + 0.00 = 1752.51。

## 4 失败处置

| 现象 | 原因码 | 处置 |
|---|---|---|
| 采信后页面无数据 | — | 看 `/events` 的「输入文件」表，文件状态 `failed` 说明编码链全失败 |
| 隔离区大量同名行 | `E_COLUMN_COUNT` / `E_BAD_JSON` | 导出端截断或多分隔符，重新导出 |
| 冲突表出现同键同版本 | `C_VERSION_CONFLICT` | 两条都不应用，回源确认版本号 |
| 导入报 409 | `C_STALE_BATCH` | 拿到的是旧导出，重新导出后再跑 |
| 快照键比预期少 | `W_PENDING_WINDOW` | 事件落在水位之后，等下一轮水位推过 |
| 水位不前进 | `W_WATERMARK_REGRESS` | 检查导出端时钟与文件版本 |

## 5 重建数据库

```bash
docker compose down -v          # 删掉命名卷 cdcwater-data
docker compose up -d --build    # 重新初始化并跑一次首次采信
```

本地直接跑（不用 Docker）：

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
CDCWATER_DB=./var/cdcwater.sqlite CDCWATER_SAMPLE_DIR=./sample_data \
  uvicorn app.main:create_app --factory --port 18201
python -m pytest -q
```

## 6 导出复核

- `events.csv`：42 行，含 `行号引用`、`规范序`、`接收序`、`顺序依据`、`原因码`、`分类`、`迟到`；
- `quarantine.csv`：10 行，含 `字段`、`原值`、`原始文本`；
- `conflicts.csv`：6 行（版本冲突 2 + 墓碑复活 4），证据用稳定的规范序位次；
- `snapshot.csv` / `snapshot.json`：18 键，活跃 16、墓碑 2；
- `manifest.json`：`run_id`、`content_digest`（批次身份）、每个输入的字节 sha256（证据）、
  `identity_basis`、`order_basis`、阶段耗时、四账。
