from __future__ import annotations

from datetime import timedelta

import pytest
from helpers import activity_dict, group_event
from nonebot.message import handle_event

from nonebot_plugin_hoyo_events import jobs
from nonebot_plugin_hoyo_events.models import now
from nonebot_plugin_hoyo_events.runtime import service, store
from nonebot_plugin_hoyo_events.store import DataStore


def _at_segments(message) -> list:
    return [int(seg.data["qq"]) for seg in message if seg.type == "at"]


def _enable(bot, monkeypatch, *uids, push_time="21:00"):
    monkeypatch.setattr(jobs, "_bots", lambda: [bot])
    store.update_group(888, push_enabled=True, push_time=push_time)
    for uid in uids:
        store.touch_player("g888", uid, f"群友{uid}")
    hour, minute = map(int, push_time.split(":"))
    return now().replace(hour=hour, minute=minute, second=0, microsecond=0)


@pytest.mark.asyncio
async def test_personal_reminders_take_turns_across_midnight(bot, offline, monkeypatch):
    start = _enable(bot, monkeypatch, 100, 200, 300, push_time="23:55")
    assert await jobs.push_due_groups(start - timedelta(minutes=1)) == []

    assert await jobs.push_due_groups(start) == [888]
    assert _at_segments(bot.messages[-1]) == [100]
    assert "15 分钟后提醒下一位" in bot.last_text
    assert await jobs.push_due_groups(start + timedelta(minutes=14)) == []

    assert await jobs.push_due_groups(start + timedelta(minutes=15)) == [888]
    assert _at_segments(bot.messages[-1]) == [200]
    assert await jobs.push_due_groups(start + timedelta(minutes=30)) == [888]
    assert _at_segments(bot.messages[-1]) == [300]
    assert "提醒下一位" not in bot.last_text
    assert await jobs.push_due_groups(start + timedelta(minutes=45)) == []


@pytest.mark.asyncio
async def test_reminder_lists_both_statuses_and_saves_numbers_for_recipient(bot, offline, monkeypatch):
    start = _enable(bot, monkeypatch, 100, 200)
    store.mark_done("g888", 100, ["ys.a#1"])
    assert await jobs.push_due_groups(start) == [888]
    assert _at_segments(bot.messages[-1]) == [100]
    pending, completed = bot.last_text.split("\n已完成（")
    assert "未完成（2 项）" in pending
    assert "02. 原神 · 砺行修远" in pending
    assert "01. 原神 · 幽境危战" in completed

    await handle_event(bot, group_event("/已完成 2", user_id=200))
    assert store.done_uids("g888", 200) == set()

    await handle_event(bot, group_event("/已完成 2", user_id=100))
    assert store.done_uids("g888", 100) == {"ys.a#1", "ys.b#1"}


@pytest.mark.asyncio
async def test_queue_skips_users_who_finish_waiting_or_mute(bot, offline, monkeypatch):
    start = _enable(bot, monkeypatch, 100, 200, 300, 400)
    assert await jobs.push_due_groups(start) == [888]
    store.mark_done("g888", 200, ["ys.a#1", "ys.b#1", "sr.a#1"])
    store.mute(888, 300)

    assert await jobs.push_due_groups(start + timedelta(minutes=15)) == [888]
    assert _at_segments(bot.messages[-1]) == [400]
    assert "提醒下一位" not in bot.last_text


@pytest.mark.asyncio
async def test_new_activity_resumes_reminders_after_everyone_finishes(bot, offline, monkeypatch):
    start = _enable(bot, monkeypatch, 100, 200)
    assert await jobs.push_due_groups(start) == [888]
    for user_id in (100, 200):
        store.mark_done("g888", user_id, ["ys.a#1", "ys.b#1", "sr.a#1"])
    assert await jobs.push_due_groups(start + timedelta(minutes=15)) == []

    store.snapshot("ys")["activities"].append(activity_dict("新活动", "ys", "ys.new#1", 5))
    assert await jobs.push_due_groups(start + timedelta(minutes=20)) == [888]
    assert _at_segments(bot.messages[-1]) == [200]
    assert "未完成（1 项）" in bot.last_text
    assert "新活动" in bot.last_text
    assert await jobs.push_due_groups(start + timedelta(minutes=35)) == [888]
    assert _at_segments(bot.messages[-1]) == [100]
    assert await jobs.push_due_groups(start + timedelta(minutes=50)) == []


@pytest.mark.asyncio
async def test_persisted_queue_resumes_without_duplicate(bot, offline, tmp_path, monkeypatch):
    start = _enable(bot, monkeypatch, 100, 200)
    monkeypatch.setattr(store, "path", tmp_path / "reminders.json")
    assert await jobs.push_due_groups(start) == [888]

    reloaded = DataStore(store.path)
    await reloaded.load()
    monkeypatch.setattr(store, "_data", reloaded.data)
    assert await jobs.push_due_groups(start + timedelta(minutes=2)) == []
    assert await jobs.push_due_groups(start + timedelta(minutes=15)) == [888]
    assert _at_segments(bot.messages[-1]) == [200]


@pytest.mark.asyncio
async def test_disabled_group_and_empty_list_stay_silent(bot, offline, monkeypatch):
    start = _enable(bot, monkeypatch)
    assert await jobs.push_due_groups(start) == []
    store.touch_player("g888", 100)
    store.update_group(888, push_enabled=False)
    assert await jobs.push_due_groups(start) == []
    assert not bot.messages


@pytest.mark.asyncio
async def test_failed_recipient_does_not_block_the_next_one(bot, offline, monkeypatch):
    start = _enable(bot, monkeypatch, 100, 200)
    original = bot.call_api

    async def fail_for_first(api, **data):
        if _at_segments(data["message"]) == [100]:
            raise RuntimeError("测试：发送失败")
        return await original(api, **data)

    monkeypatch.setattr(bot, "call_api", fail_for_first)
    for minute in range(jobs.MAX_PUSH_ATTEMPTS):
        assert await jobs.push_due_groups(start + timedelta(minutes=minute)) == []
    assert await jobs.push_due_groups(start + timedelta(minutes=jobs.MAX_PUSH_ATTEMPTS)) == [888]
    assert _at_segments(bot.messages[-1]) == [200]


@pytest.mark.asyncio
async def test_refresh_all_uses_source(monkeypatch):
    calls = {}

    async def fake_ensure(force=False):
        calls["force"] = force
        return {"ys": "ok"}

    monkeypatch.setattr(service, "ensure_data", fake_ensure)
    result = await jobs.refresh_all(force=True)
    assert result == {"ys": "ok"}
    assert calls["force"] is True
