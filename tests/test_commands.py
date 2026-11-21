from __future__ import annotations

import pytest
from helpers import group_event, private_event
from nonebot.message import handle_event

from nonebot_plugin_hoyo_events.runtime import service, store


@pytest.mark.asyncio
async def test_list_command(bot, offline):
    await handle_event(bot, group_event("/活动"))
    assert bot.sent
    text = bot.sent[0][1]["message"].extract_plain_text()
    assert "幽境危战" in text
    assert "虚构叙事" in text
    assert "剩 4 天" in text
    assert "已完成" in text


@pytest.mark.asyncio
async def test_list_single_game(bot, offline):
    await handle_event(bot, group_event("/原神活动"))
    text = bot.sent[0][1]["message"].extract_plain_text()
    assert "幽境危战" in text
    assert "虚构叙事" not in text


@pytest.mark.asyncio
async def test_done_by_index(bot, offline):
    await handle_event(bot, group_event("/活动"))
    await handle_event(bot, group_event("/已完成 1"))

    assert store.done_uids("g888", 100) == {"ys.a#1"}
    text = bot.sent[-1][1]["message"].extract_plain_text()
    assert "已记录完成 1 个活动" in text
    assert "幽境危战" in text


@pytest.mark.asyncio
async def test_done_does_not_affect_other_events(bot, offline):
    store.touch_player("g888", 100, "小明")
    store.touch_player("g888", 200, "小红")

    await handle_event(bot, group_event("/活动"))
    await handle_event(bot, group_event("/已完成 1"))

    view = service.build_view(None, "g888", 100)
    targets = service.at_targets("g888", view.activities(), 888)
    first = next(t for t in targets if t.activity.uid == "ys.a#1")
    assert first.users == [200]
    others = [t for t in targets if t.activity.uid != "ys.a#1"]
    assert others and all(100 in t.users for t in others)


@pytest.mark.asyncio
async def test_done_by_name(bot, offline):
    await handle_event(bot, group_event("/已完成 虚构叙事", user_id=200))
    assert store.done_uids("g888", 200) == {"sr.a#1"}


@pytest.mark.asyncio
async def test_done_all(bot, offline):
    await handle_event(bot, group_event("/已完成 全部", user_id=300))
    assert store.done_uids("g888", 300) == {"ys.a#1", "ys.b#1", "sr.a#1"}


@pytest.mark.asyncio
async def test_done_without_args_shows_pending(bot, offline):
    await handle_event(bot, group_event("/已完成"))
    text = bot.sent[-1][1]["message"].extract_plain_text()
    assert "你还没完成的活动" in text
    assert store.done_uids("g888", 100) == set()


@pytest.mark.asyncio
async def test_pending_command(bot, offline):
    store.mark_done("g888", 100, ["ys.a#1"])
    await handle_event(bot, group_event("/未完成"))
    text = bot.sent[-1][1]["message"].extract_plain_text()
    assert "幽境危战" not in text
    assert "虚构叙事" in text
    assert "1/3" in text


@pytest.mark.asyncio
async def test_undo_command(bot, offline):
    store.mark_done("g888", 100, ["ys.a#1"])
    await handle_event(bot, group_event("/活动"))
    await handle_event(bot, group_event("/取消完成 1"))
    text = bot.sent[-1][1]["message"].extract_plain_text()
    assert "取消" in text
    assert store.done_uids("g888", 100) == set()


@pytest.mark.asyncio
async def test_undo_list_restores_the_right_activity_after_viewing_pending(bot, offline):
    store.mark_done("g888", 200, ["ys.b#1"])
    await handle_event(bot, group_event("/活动"))
    await handle_event(bot, group_event("/已完成 2"))
    assert "标错了" in bot.last_text

    await handle_event(bot, group_event("/未完成"))
    assert "砺行修远" not in bot.last_text
    await handle_event(bot, group_event("/取消完成"))
    assert "你已完成的活动" in bot.last_text
    assert "1. 砺行修远" in bot.last_text
    assert "幽境危战" not in bot.last_text

    await handle_event(bot, group_event("/取消完成 1"))
    assert "已移回未完成列表" in bot.last_text
    assert store.done_uids("g888", 100) == set()
    assert store.done_uids("g888", 200) == {"ys.b#1"}
    await handle_event(bot, group_event("/未完成"))
    assert "砺行修远" in bot.last_text


@pytest.mark.asyncio
async def test_undo_pending_activity_reports_that_there_is_nothing_to_undo(bot, offline):
    store.mark_done("g888", 100, ["ys.b#1"])
    await handle_event(bot, group_event("/取消完成 幽境危战"))
    assert "没有标记完成" in bot.last_text
    assert store.done_uids("g888", 100) == {"ys.b#1"}


@pytest.mark.asyncio
async def test_private_list(bot, offline):
    await handle_event(bot, private_event("/活动"))
    assert bot.sent
    assert "幽境危战" in bot.sent[0][1]["message"].extract_plain_text()


@pytest.mark.asyncio
async def test_private_done(bot, offline):
    await handle_event(bot, private_event("/活动"))
    await handle_event(bot, private_event("/已完成 1"))
    assert store.done_uids("private_100", 100) == {"ys.a#1"}


@pytest.mark.asyncio
async def test_reminder_permission(bot, offline):
    await handle_event(bot, group_event("/活动提醒 开", role="member"))
    assert "管理员" in bot.sent[-1][1]["message"].extract_plain_text()
    assert not store.group(888).get("push_enabled")

    await handle_event(bot, group_event("/活动提醒 开", user_id=50, role="admin"))
    assert store.group(888).get("push_enabled") is True
    assert "已开启" in bot.sent[-1][1]["message"].extract_plain_text()


@pytest.mark.asyncio
async def test_reminder_time(bot, offline):
    await handle_event(bot, group_event("/活动提醒 时间 22:30", user_id=50, role="owner"))
    record = store.group(888)
    assert record["push_time"] == "22:30"
    assert record["push_enabled"] is True


@pytest.mark.asyncio
async def test_reminder_status(bot, offline):
    await handle_event(bot, group_event("/活动提醒 状态"))
    text = bot.sent[-1][1]["message"].extract_plain_text()
    assert "每日推送" in text
    assert "推送时间" in text


@pytest.mark.asyncio
async def test_member_can_set_own_countdown_without_affecting_other_users_or_groups(bot, offline):
    await handle_event(bot, group_event("/活动提醒 个人 倒计时 3"))
    assert "结束前 3 天" in bot.last_text
    assert store.personal_reminder_days(888, 100) == 3
    assert store.personal_reminder_days(888, 200) is None
    assert store.personal_reminder_days(999, 100) is None
    assert not store.player("g888", 100)

    await handle_event(bot, group_event("/活动提醒 状态"))
    assert "我的提醒：活动结束前 3 天" in bot.last_text

    await handle_event(bot, group_event("/活动提醒 个人 倒计时 -1"))
    assert "0–365" in bot.last_text
    assert store.personal_reminder_days(888, 100) == 3

    await handle_event(bot, group_event("/活动提醒 个人 每天"))
    assert "每天提醒未完成活动" in bot.last_text
    assert store.personal_reminder_days(888, 100) is None


@pytest.mark.asyncio
async def test_mute_command(bot, offline):
    store.touch_player("g888", 100, "小明")
    await handle_event(bot, group_event("/活动免打扰"))
    assert 100 in store.muted(888)
    view = service.build_view(None, "g888", 100)
    targets = service.at_targets("g888", view.activities(), 888)
    assert all(100 not in t.users for t in targets)

    await handle_event(bot, group_event("/活动免打扰"))
    assert 100 not in store.muted(888)


@pytest.mark.asyncio
async def test_help(bot, offline):
    await handle_event(bot, group_event("/活动帮助"))
    text = bot.sent[-1][1]["message"].extract_plain_text()
    assert "已完成" in text and "活动提醒" in text


@pytest.mark.asyncio
async def test_unknown_index(bot, offline):
    await handle_event(bot, group_event("/已完成 99"))
    text = bot.sent[-1][1]["message"].extract_plain_text()
    assert "找不到" in text


@pytest.mark.asyncio
async def test_no_data(bot, clean_store):
    await handle_event(bot, group_event("/活动"))
    text = bot.sent[-1][1]["message"].extract_plain_text()
    assert "拿不到活动数据" in text


@pytest.mark.asyncio
async def test_help_lists_every_command(bot, offline):
    await handle_event(bot, group_event("/活动帮助"))
    text = bot.last_text
    for keyword in (
        "活动关注",
        "活动名单",
        "活动设置",
        "已完成",
        "未完成",
        "取消完成",
        "活动刷新",
    ):
        assert keyword in text, keyword
    assert "本群当前" in text


@pytest.mark.asyncio
async def test_help_via_activity_command(bot, offline):
    await handle_event(bot, group_event("/活动 帮助"))
    assert "指令表" in bot.last_text


@pytest.mark.asyncio
async def test_settings_via_activity_command(bot, offline):
    await handle_event(bot, group_event("/活动 设置"))
    assert "本群活动设置" in bot.last_text


@pytest.mark.asyncio
async def test_help_menu_alias(bot, offline):
    await handle_event(bot, group_event("/活动菜单"))
    assert "指令表" in bot.last_text
    await handle_event(bot, group_event("/活动指令"))
    assert "指令表" in bot.last_text
