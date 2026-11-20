from __future__ import annotations

import json

import pytest
from helpers import group_event
from nonebot.message import handle_event

from nonebot_plugin_hoyo_events.runtime import service, source, store


@pytest.mark.asyncio
async def test_settings_show(bot, offline):
    await handle_event(bot, group_event("/活动设置"))
    text = bot.last_text
    assert "本群活动设置" in text
    assert "每日推送" in text
    assert "推送时间" in text
    assert "加急天数" in text


@pytest.mark.asyncio
async def test_settings_help(bot, offline):
    await handle_event(bot, group_event("/活动设置 帮助"))
    text = bot.last_text
    assert "可以这样改设置" in text
    assert "加急天数" in text


@pytest.mark.asyncio
async def test_set_push_time(bot, offline):
    await handle_event(bot, group_event("/活动设置 时间 22:45", user_id=50, role="admin"))
    assert store.group(888)["push_time"] == "22:45"
    assert service.group_settings(888).hour == 22
    assert service.group_settings(888).minute == 45


@pytest.mark.asyncio
async def test_set_remind_days(bot, offline):
    await handle_event(bot, group_event("/活动设置 加急天数 7", user_id=50, role="owner"))
    assert service.group_settings(888).remind_days == 7


@pytest.mark.asyncio
async def test_set_remind_days_out_of_range(bot, offline):
    await handle_event(bot, group_event("/活动设置 加急天数 99", user_id=50, role="owner"))
    assert "取值范围" in bot.last_text
    assert service.group_settings(888).remind_days == 3


@pytest.mark.asyncio
async def test_member_cannot_change_by_default(bot, offline):
    await handle_event(bot, group_event("/活动设置 时间 23:00"))
    assert "管理员" in bot.last_text
    assert "push_time" not in store.group(888)


@pytest.mark.asyncio
async def test_permission_open_to_everyone(bot, offline):
    await handle_event(bot, group_event("/活动设置 权限 所有人", user_id=50, role="owner"))
    assert service.group_settings(888).push_admin_only is False

    await handle_event(bot, group_event("/活动设置 时间 23:30"))
    assert store.group(888)["push_time"] == "23:30"


@pytest.mark.asyncio
async def test_unknown_option(bot, offline):
    await handle_event(bot, group_event("/活动设置 随便 1", user_id=50, role="owner"))
    assert "没有" in bot.last_text


@pytest.mark.asyncio
async def test_set_watch_options(bot, offline):
    await handle_event(bot, group_event("/活动设置 名单上限 20", user_id=50, role="owner"))
    assert service.group_settings(888).max_watch == 20

    await handle_event(bot, group_event("/活动设置 允许@加人 关", user_id=50, role="owner"))
    assert service.group_settings(888).allow_add_others is False


@pytest.mark.asyncio
async def test_global_settings_superuser(bot, offline, monkeypatch):
    monkeypatch.setattr(store, "commit", _noop_commit)
    await handle_event(bot, group_event("/活动设置 全局 缓存 600", user_id=10000))
    assert "全局「缓存秒数」" in bot.last_text
    assert service.settings.global_value("cache_ttl") == 600
    assert source.ttl == 600


@pytest.mark.asyncio
async def test_global_settings_needs_superuser(bot, offline):
    await handle_event(bot, group_event("/活动设置 全局 缓存 600", user_id=50, role="owner"))
    assert "管理员指令" in bot.last_text


@pytest.mark.asyncio
async def test_global_api_base(bot, offline, monkeypatch):
    monkeypatch.setattr(store, "commit", _noop_commit)
    await handle_event(
        bot, group_event("/活动设置 全局 数据源 https://example.com/api/activity", user_id=10000)
    )
    assert service.settings.global_value("api_base") == "https://example.com/api/activity"
    assert source.api_base == "https://example.com/api/activity"


def test_migrate_old_group_fields(tmp_path):
    from nonebot_plugin_hoyo_events.store import DataStore

    path = tmp_path / "store.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "groups": {"123": {"enabled": True, "hour": 22, "minute": 5, "muted": []}},
                "players": {},
                "completions": {},
                "snapshots": {},
                "registry": {},
                "meta": {},
            }
        ),
        "utf-8",
    )
    fresh = DataStore(path)

    import asyncio

    asyncio.run(fresh.load())
    record = fresh.group(123)
    assert record["push_enabled"] is True
    assert record["push_time"] == "22:05"
    assert "enabled" not in record and "hour" not in record


@pytest.mark.asyncio
async def test_push_time_change_takes_effect(bot, offline, monkeypatch):
    """命令改完时间，推送任务立刻按新时间判断。"""

    from nonebot_plugin_hoyo_events import jobs
    from nonebot_plugin_hoyo_events.models import now

    await handle_event(bot, group_event("/活动设置 推送 开", user_id=50, role="owner"))
    await handle_event(bot, group_event("/活动设置 时间 23:59", user_id=50, role="owner"))
    store.touch_player("g888", 100, "小明")
    monkeypatch.setattr(jobs, "_bots", lambda: [bot])

    early = now().replace(hour=1, minute=0)
    assert await jobs.push_due_groups(early) == []
    late = now().replace(hour=23, minute=59)
    assert await jobs.push_due_groups(late) == [888]


async def _noop_commit():
    return None


@pytest.mark.asyncio
async def test_refresh_interval_is_runtime_configurable(bot, offline, monkeypatch):
    from datetime import timedelta

    from nonebot_plugin_hoyo_events import jobs
    from nonebot_plugin_hoyo_events.models import iso, now

    calls = []

    async def fake_ensure(force: bool = False) -> dict:
        calls.append(force)
        store.meta("last_refresh", iso(now()))
        return {}

    monkeypatch.setattr(service, "ensure_data", fake_ensure)
    service.settings.set_global("refresh_interval", 60)

    assert await jobs.refresh_if_due(now()) is True
    assert await jobs.refresh_if_due(now()) is False  # 刚刷过，不重复
    assert len(calls) == 1

    store.meta("last_refresh", iso(now() - timedelta(minutes=61)))
    assert await jobs.refresh_if_due(now()) is True
    assert len(calls) == 2

    service.settings.set_global("refresh_interval", 0)  # 0 = 关闭后台刷新
    store.meta("last_refresh", iso(now() - timedelta(days=1)))
    assert await jobs.refresh_if_due(now()) is False
