"""测试固定装置：临时库、样例目录、可复用的归一构造器。

约定：所有时间断言用样例里的固定时刻，不读 datetime.now()。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config                                  # noqa: E402
from app.main import create_app                         # noqa: E402
from app.modules.m1_events.reader import RawRow         # noqa: E402
from app.modules.m2_schema import service as m2_service  # noqa: E402
from app.modules.m6_batches import service as batch_service  # noqa: E402
from app.store import Store                             # noqa: E402

SAMPLE_DIR = ROOT / "sample_data"


@pytest.fixture()
def settings(tmp_path) -> config.Settings:
    return config.Settings(db_path=str(tmp_path / "cdcwater.sqlite"),
                           sample_dir=str(SAMPLE_DIR),
                           late_window_minutes=10)


@pytest.fixture()
def store(settings) -> Store:
    return Store(settings.db_path)


@pytest.fixture()
def run(settings, store) -> dict:
    return batch_service.run(settings=settings, store=store, source="all")


@pytest.fixture()
def batch_id(run) -> str:
    return run["batch"]["batch_id"]


@pytest.fixture()
def client(settings):
    app = create_app(settings, seed=True)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def norm_factory():
    """按真实归一逻辑构造一条事件，避免测试里手写不一致的字段。"""
    counter = {"seq": 0}

    def make(table="orders", pk="1001", version=1, op="upsert",
             event_time="2026-11-15T08:00:00Z", payload=None, schema="v2",
             seq=None):
        counter["seq"] += 1
        raw = {"table": table, "pk": pk, "version": version, "op": op,
               "event_time": event_time, "schema": schema,
               "payload": payload if payload is not None else {
                   "status": "新建", "amount": "10.00"}}
        row = RawRow(file="fixture", lineno=counter["seq"], text="", raw=raw)
        row.seq = seq if seq is not None else counter["seq"]
        return m2_service.normalize_row(row)

    return make


@pytest.fixture()
def tmp_samples(tmp_path):
    """临时样例目录：测试可以放自己的脏文件，走同一条采信路径。"""
    directory = tmp_path / "samples"
    directory.mkdir()
    return directory
