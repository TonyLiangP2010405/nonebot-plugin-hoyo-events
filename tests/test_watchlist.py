from __future__ import annotations

import pytest
from helpers import group_event
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.message import handle_event

from nonebot_plugin_hoyo_events.runtime import config, service, store


def watch_message(*user_ids) -> Message:
    message = Message("/活动关注")
    for uid in user_ids:
        message += MessageSegment.text(" ")
        message += MessageSegment.at(uid)
    return message


def unwatch_message(*user_ids) -> Message:
    message = Message("/活动取关")
    for uid in user_ids:
        message += MessageSegment.text(" ")
        message += MessageSegment.at(uid)
    return message


@pytest.mark.asyncio
async def test_watch_self(bot, offline):
    await handle_event(bot, group_event("/活动关注"))
    assert store.player_count("g888") == 1
    assert store.player("g888", 100)["nick"] == "用户100"
    assert "已加入提醒名单" in bot.last_text


@pytest.mark.asyncio
async def test_watch_others_by_mention(bot, offline):
    await handle_event(bot, group_event(watch_message(200, 300)))

    assert store.player_count("g888") == 3  # 自己 + 两个被 @ 的人
    assert store.player("g888", 200)["added_by"] == 100
    assert store.player("g888", 200)["nick"] == "群名片200"

    message = bot.messages[-1]
    at_ids = [int(seg.data["qq"]) for seg in message if seg.type == "at"]
    assert at_ids == [200, 300]
    assert "群名片200" in message.extract_plain_text()


@pytest.mark.asyncio
async def test_watch_others_disabled(bot, offline, monkeypatch):
    monkeypatch.setattr(config, "hoyo_events_allow_add_others", False)
    await handle_event(bot, group_event(watch_message(200)))
    assert store.player("g888", 200) == {}
    assert "只允许关注自己" in bot.last_text


@pytest.mark.asyncio
async def test_watch_respects_limit(bot, offline, monkeypatch):
    monkeypatch.setattr(config, "hoyo_events_max_watch", 2)
    await handle_event(bot, group_event(watch_message(200, 300, 400)))
    assert "名单已满" in bot.last_text
    assert store.player_count("g888") == 0


@pytest.mark.asyncio
async def test_watch_ignores_at_all(bot, offline):
    message = Message("/活动关注 ")
    message += MessageSegment.at("all")
    await handle_event(bot, group_event(message))
    assert store.player_count("g888") == 1
    assert store.player("g888", 100) != {}


@pytest.mark.asyncio
async def test_watchlist_shows_pending(bot, offline):
    await handle_event(bot, group_event(watch_message(200)))
    await handle_event(bot, group_event("/已完成 全部", user_id=200))

    await handle_event(bot, group_event("/活动名单", user_id=200))
    text = bot.last_text
    assert "本群提醒名单" in text
    assert "全部完成" in text
    assert "未完成 3" in text


@pytest.mark.asyncio
async def test_unwatch_self(bot, offline):
    await handle_event(bot, group_event("/活动关注"))
    await handle_event(bot, group_event("/活动取关"))
    assert store.player_count("g888") == 0
    assert "已移出" in bot.last_text


@pytest.mark.asyncio
async def test_unwatch_other_by_stranger_denied(bot, offline):
    await handle_event(bot, group_event(watch_message(200), user_id=100))
    # 300 想把 200 踢出去，但 300 不是加人的人
    await handle_event(bot, group_event(unwatch_message(200), user_id=300))
    assert store.player("g888", 200) != {}
    assert "没权限" in bot.last_text


@pytest.mark.asyncio
async def test_unwatch_other_by_adder_allowed(bot, offline):
    await handle_event(bot, group_event(watch_message(200), user_id=100))
    await handle_event(bot, group_event(unwatch_message(200), user_id=100))
    assert store.player("g888", 200) == {}
    assert "已移出" in bot.last_text


@pytest.mark.asyncio
async def test_unwatch_other_by_admin_allowed(bot, offline):
    await handle_event(bot, group_event(watch_message(200), user_id=100))
    await handle_event(bot, group_event(unwatch_message(200), user_id=50, role="admin"))
    assert store.player("g888", 200) == {}


@pytest.mark.asyncio
async def test_added_member_is_at_in_push(bot, offline, monkeypatch):
    """核心诉求：没发过命令的人，被 @ 加入名单后也会被推送 @。"""

    await handle_event(bot, group_event(watch_message(200)))
    monkeypatch.setattr("nonebot_plugin_hoyo_events.jobs._bots", lambda: [bot])

    from nonebot_plugin_hoyo_events import jobs

    await jobs.push_group_once(888, bot=bot)
    message = bot.messages[-1]
    at_ids = [int(seg.data["qq"]) for seg in message if seg.type == "at"]
    assert 200 in at_ids
    assert 100 in at_ids


@pytest.mark.asyncio
async def test_push_can_be_enabled_by_member_when_configured(bot, offline, monkeypatch):
    monkeypatch.setattr(config, "hoyo_events_push_admin_only", False)
    await handle_event(bot, group_event("/活动提醒 开", role="member"))
    assert store.group(888)["push_enabled"] is True
    assert "已开启" in bot.last_text


@pytest.mark.asyncio
async def test_push_still_admin_only_by_default(bot, offline):
    await handle_event(bot, group_event("/活动提醒 开", role="member"))
    assert not store.group(888).get("push_enabled")
    assert "管理员" in bot.last_text


@pytest.mark.asyncio
async def test_watch_requires_group(bot, offline):
    from helpers import private_event

    await handle_event(bot, private_event("/活动关注"))
    assert "群里" in bot.last_text


def test_help_mentions_watch(bot, offline):
    from nonebot_plugin_hoyo_events.formatter import render_help

    text = render_help("21:00", 3)
    assert "活动关注" in text and "活动名单" in text


def test_service_still_singleton(bot, offline):
    assert service.store is store
