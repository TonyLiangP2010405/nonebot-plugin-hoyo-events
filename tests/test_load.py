from __future__ import annotations

import nonebot
import pytest

from nonebot_plugin_hoyo_events.commands import _split_tokens
from nonebot_plugin_hoyo_events.runtime import OVERRIDE_PATH, parse_push_time, service, store


def test_plugin_is_loaded():
    plugin = nonebot.get_plugin("nonebot_plugin_hoyo_events")
    if plugin is None:
        loaded = sorted(p.module_name for p in nonebot.get_loaded_plugins())
        raise AssertionError(f"插件未注册，已加载的插件：{loaded}")
    assert plugin.metadata is not None
    assert plugin.metadata.name == "米哈游活动助手"
    assert "~onebot.v11" in plugin.metadata.supported_adapters


def test_override_template_exists():
    assert OVERRIDE_PATH.exists()
    assert "ys" in OVERRIDE_PATH.read_text("utf-8")


def test_parse_push_time():
    assert parse_push_time("21:30") == (21, 30)
    assert parse_push_time("7:05") == (7, 5)
    assert parse_push_time("21：30") == (21, 30)
    assert parse_push_time("24:00") is None
    assert parse_push_time("21:99") is None
    assert parse_push_time("abc") is None


def test_split_tokens():
    assert _split_tokens("1 3") == ["1", "3"]
    assert _split_tokens("1,2，3、4") == ["1", "2", "3", "4"]
    assert _split_tokens("  幽境危战  ") == ["幽境危战"]
    assert _split_tokens("") == []


def test_service_singleton():
    assert service.store is store


@pytest.mark.asyncio
async def test_store_load_without_data():
    await service.startup()
    assert isinstance(store.data, dict)
    assert "completions" in store.data
