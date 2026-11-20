from __future__ import annotations

from datetime import timedelta

import pytest
from helpers import SELF_ID

from nonebot_plugin_hoyo_events import jobs
from nonebot_plugin_hoyo_events.models import now
from nonebot_plugin_hoyo_events.runtime import service, store


def _at_segments(message) -> list:
    return [int(seg.data["qq"]) for seg in message if seg.type == "at"]


@pytest.mark.asyncio
async def test_build_group_push(bot, offline):
    store.touch_player("g888", 100, "小明")
    store.touch_player("g888", 200, "小红")
    store.mark_done("g888", 100, ["ys.a#1"])

    payload = jobs.build_group_push(888)
    assert payload is not None
    assert "幽境危战" in payload.head
    labels = {target.activity.uid: target.users for target in payload.targets}
    assert labels["ys.a#1"] == [200]  # 100 已完成这个活动
    assert 100 in labels["ys.b#1"]  # 其它活动照常 @


@pytest.mark.asyncio
async def test_build_group_push_without_events(bot, clean_store):
    assert jobs.build_group_push(888) is None


@pytest.mark.asyncio
async def test_push_group_once(bot, offline, monkeypatch):
    store.touch_player("g888", 100, "小明")
    monkeypatch.setattr(jobs, "_bots", lambda: [bot])

    assert await jobs.push_group_once(888, bot=bot) is True
    message = bot.messages[-1]
    assert "今日活动提醒" in message.extract_plain_text()
    assert _at_segments(message) == [100, 100, 100]  # 三个活动都没完成


@pytest.mark.asyncio
async def test_push_respects_completion_per_event(bot, offline, monkeypatch):
    store.touch_player("g888", 100, "小明")
    store.mark_done("g888", 100, ["ys.a#1", "ys.b#1"])

    await jobs.push_group_once(888, bot=bot)
    message = bot.messages[-1]
    assert _at_segments(message) == [100]  # 只剩「虚构叙事」需要 @


@pytest.mark.asyncio
async def test_push_skips_users_who_finished_everything(bot, offline):
    store.touch_player("g888", 100, "小明")
    store.touch_player("g888", 200, "小红")
    store.mark_done("g888", 100, ["ys.a#1", "ys.b#1", "sr.a#1"])

    await jobs.push_group_once(888, bot=bot)
    message = bot.messages[-1]
    assert 100 not in _at_segments(message)
    assert _at_segments(message).count(200) == 3


@pytest.mark.asyncio
async def test_push_due_groups_once_per_day(bot, offline, monkeypatch):
    store.update_group(888, push_enabled=True, push_time="00:00")
    store.touch_player("g888", 100, "小明")
    monkeypatch.setattr(jobs, "_bots", lambda: [bot])
    monkeypatch.setattr(jobs, "push_group_once", _fake_push(bot))

    pushed = await jobs.push_due_groups(now())
    assert pushed == [888]
    assert store.group(888)["last_push"] == now().date().isoformat()

    again = await jobs.push_due_groups(now())
    assert again == []


@pytest.mark.asyncio
async def test_push_due_groups_respects_time(bot, offline, monkeypatch):
    store.update_group(888, push_enabled=True, push_time="23:59")
    store.touch_player("g888", 100, "小明")
    monkeypatch.setattr(jobs, "push_group_once", _fake_push(bot))

    early = now().replace(hour=0, minute=1)
    assert await jobs.push_due_groups(early) == []
    late = now().replace(hour=23, minute=59)
    assert await jobs.push_due_groups(late) == [888]


@pytest.mark.asyncio
async def test_push_due_groups_disabled(bot, offline, monkeypatch):
    store.update_group(888, push_enabled=False)
    monkeypatch.setattr(jobs, "push_group_once", _fake_push(bot))
    assert await jobs.push_due_groups(now()) == []


@pytest.mark.asyncio
async def test_push_retries_then_gives_up(bot, offline, monkeypatch):
    store.update_group(888, push_enabled=True, push_time="00:00")
    store.touch_player("g888", 100, "小明")

    async def fail(group_id, bot=None):
        return False

    monkeypatch.setattr(jobs, "push_group_once", fail)
    for _ in range(jobs.MAX_PUSH_ATTEMPTS):
        assert await jobs.push_due_groups(now()) == []

    # 达到重试上限后当天不再尝试
    assert store.group(888)["last_push"] == now().date().isoformat()
    assert await jobs.push_due_groups(now()) == []


def _fake_push(fake_bot):
    async def _push(group_id, bot=None):
        payload = jobs.build_group_push(group_id)
        if payload is None:
            return False
        fake_bot.sent.append(("send_group_msg", {"message": "pushed", "group_id": group_id}))
        return True

    return _push


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


def test_view_expiry_between_push_and_query(bot, offline):
    """群里推送的编号要能被人用来「已完成 N」。"""

    store.touch_player("g888", 100, "小明")
    payload = jobs.build_group_push(888)
    assert payload is not None

    view = service.get_view("g888")
    assert view is not None
    assert view.entries[0].activity.uid == "ys.a#1"
    assert (now() - view.at) < timedelta(minutes=1)
    assert SELF_ID  # 便于 lint
