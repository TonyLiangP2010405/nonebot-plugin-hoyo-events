from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from pathlib import Path

# 必须在导入插件前设置数据目录，避免污染真实的 localstore 目录
DATA_DIR = Path(tempfile.mkdtemp(prefix="hoyo-events-test-"))
os.environ["HOYO_EVENTS_DATA_DIR"] = str(DATA_DIR)

import nonebot  # noqa: E402
import pytest  # noqa: E402

nonebot.init(driver="~none", command_start={"/"}, superusers={"10000"})

# 注意：这里故意用普通 import 而不是 nonebot.load_plugin。
# NoneBot 的插件加载器要求模块「加载前没有被 import 过」，在 pytest 里
# 很容易因为收集顺序踩到这个坑；插件能否被 NoneBot 正常加载由
# tests/test_load.py 在独立子进程里验证。
from nonebot_plugin_hoyo_events.models import Activity, now  # noqa: E402
from nonebot_plugin_hoyo_events.store import DataStore  # noqa: E402


@pytest.fixture
def store(tmp_path) -> DataStore:
    return DataStore(tmp_path / "store.json")


@pytest.fixture
def make_activity():
    def _make(
        name: str = "测试活动",
        game: str = "ys",
        start_offset: int = -1,
        end_offset: int = 10,
        uid: str = "",
        generation: int = 1,
    ) -> Activity:
        at = now()
        return Activity(
            uid=uid or f"{game}.{abs(hash(name)) % 10**8:08x}#{generation}",
            game=game,
            name=name,
            start=at + timedelta(days=start_offset),
            end=at + timedelta(days=end_offset),
            generation=generation,
        )

    return _make


# ---------------------------------------------------------------------------
# nonebug 作为 pytest 插件会自动 nonebot.init() 并启动 lifespan，
# 这会和本仓库的显式初始化冲突、还会真的联网拉活动数据。
# 这里用同名 fixture 覆盖掉它，只保留 pytest 本身的能力。
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _nonebot_init():
    yield


@pytest.fixture(scope="session", autouse=True)
def after_nonebot_init():
    yield


@pytest.fixture(scope="session", autouse=True)
def nonebug_init():
    yield


# --------------------------------------------------------------------------- 公共 fixture
@pytest.fixture
def bot():
    from helpers import make_bot

    return make_bot()


@pytest.fixture(autouse=True)
def text_mode(monkeypatch):
    # 既有测试验证文字内容，卡片测试单独开启图片模式。
    from nonebot_plugin_hoyo_events.runtime import config

    monkeypatch.setattr(config, "hoyo_events_cards", False)


@pytest.fixture
def offline(monkeypatch):
    """禁止测试联网、重置内存数据，并写入三个游戏的假快照。"""

    from helpers import seed_store

    from nonebot_plugin_hoyo_events.runtime import service, store
    from nonebot_plugin_hoyo_events.store import default_data

    store._data = default_data()
    seed_store(store)

    async def fake_ensure(force: bool = False) -> dict:
        return {}

    monkeypatch.setattr(service, "ensure_data", fake_ensure)
    monkeypatch.setattr(service, "_started", True)
    return store


@pytest.fixture
def clean_store(monkeypatch):
    """空白数据（没有任何快照/玩家）。"""

    from nonebot_plugin_hoyo_events.runtime import service, store
    from nonebot_plugin_hoyo_events.store import default_data

    store._data = default_data()

    async def fake_ensure(force: bool = False) -> dict:
        return {}

    monkeypatch.setattr(service, "ensure_data", fake_ensure)
    monkeypatch.setattr(service, "_started", True)
    return store
