from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap

import nonebot
import pytest

from nonebot_plugin_hoyo_events import __plugin_meta__
from nonebot_plugin_hoyo_events.commands import _split_tokens
from nonebot_plugin_hoyo_events.runtime import OVERRIDE_PATH, parse_push_time, service, store


def test_plugin_metadata():
    assert __plugin_meta__ is not None
    assert __plugin_meta__.name == "米哈游活动助手"
    assert __plugin_meta__.type == "application"
    assert "~onebot.v11" in __plugin_meta__.supported_adapters
    assert __plugin_meta__.homepage.startswith("https://github.com/")


def test_plugin_can_be_loaded_by_nonebot():
    """在干净的子进程里跑一次真正的 nonebot.load_plugin。"""

    code = textwrap.dedent(
        """
        import nonebot

        nonebot.init(driver="~none", command_start={"/"})
        plugin = nonebot.load_plugin("nonebot_plugin_hoyo_events")
        assert plugin is not None, "load_plugin 返回 None"
        assert plugin.metadata is not None
        print("LOADED", plugin.metadata.name)
        """
    )
    env = {
        **os.environ,
        "HOYO_EVENTS_DATA_DIR": tempfile.mkdtemp(prefix="hoyo-load-test-"),
        "COMMAND_START": '[""]',
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "LOADED" in result.stdout


def test_matchers_registered():
    from nonebot.matcher import Matcher

    from nonebot_plugin_hoyo_events import commands

    names = [
        "help_matcher",
        "list_matcher",
        "game_matcher",
        "done_matcher",
        "todo_matcher",
        "undo_matcher",
        "mute_matcher",
        "push_matcher",
        "refresh_matcher",
    ]
    matcher_types = [getattr(commands, name) for name in names]
    assert len(set(matcher_types)) == len(names)
    assert all(issubclass(item, Matcher) for item in matcher_types)
    assert {item.module.__name__ for item in matcher_types} == {
        "nonebot_plugin_hoyo_events.commands"
    }


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


def test_nonebot_is_initialized():
    assert nonebot.get_driver() is not None
