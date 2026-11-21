from __future__ import annotations

from base64 import b64decode
from datetime import timedelta
from io import BytesIO

import pytest
from helpers import group_event
from nonebot.message import handle_event
from PIL import Image

from nonebot_plugin_hoyo_events import cards, jobs, messaging
from nonebot_plugin_hoyo_events.runtime import config, service, store


def _png(message):
    assert message[0].type == "image"
    encoded = message[0].data["file"].removeprefix("base64://")
    with Image.open(BytesIO(b64decode(encoded))) as image:
        assert image.format == "PNG"
        assert image.width == cards.WIDTH
        return image.copy()


async def _no_avatar(user_id):
    return None


@pytest.mark.asyncio
async def test_activity_card_and_push_keep_indices_and_mentions(bot, offline, monkeypatch):
    if not cards._font_path(config.hoyo_events_card_font):
        pytest.skip("系统没有可用的中文字体")
    monkeypatch.setattr(config, "hoyo_events_cards", True)
    monkeypatch.setattr(messaging, "_fetch_qq_avatar", _no_avatar)
    store.touch_player("g888", 200)
    store.mark_done("g888", 200, ["ys.a#1", "ys.b#1", "sr.a#1"])
    await handle_event(bot, group_event("/活动 提醒"))
    message = bot.messages[-1]
    _png(message)
    assert [item.data["qq"] for item in message if item.type == "at"] == ["100"]

    await handle_event(bot, group_event("/已完成 1"))
    _png(bot.messages[-1])
    assert store.done_uids("g888", 100) == {"ys.a#1"}

    assert await jobs.push_group_once(888, bot)
    message = bot.messages[-1]
    _png(message)
    assert [item.data["qq"] for item in message if item.type == "at"] == ["100"]
    assert len([item for item in message if item.type == "image"]) == 1


@pytest.mark.asyncio
async def test_personal_reminder_shows_avatar_for_each_mentioned_user(bot, offline, monkeypatch):
    if not cards._font_path(config.hoyo_events_card_font):
        pytest.skip("系统没有可用的中文字体")
    monkeypatch.setattr(config, "hoyo_events_cards", True)
    store.update_group(888, push_time="00:00")
    for uid in (100, 200):
        store.touch_player("g888", uid, f"群友{uid}")

    colors = {100: (40, 90, 170), 200: (180, 60, 90)}

    async def avatar_for(uid):
        data = BytesIO()
        Image.new("RGB", (100, 100), colors[uid]).save(data, format="PNG")
        return data.getvalue()

    monkeypatch.setattr(messaging, "_fetch_qq_avatar", avatar_for)
    start = jobs.now()
    x, y = cards.AVATAR_CENTER
    avatar_center = (x + cards.MARGIN, y + cards.MARGIN)
    for i, uid in enumerate(colors):
        assert await jobs.push_group_once(888, bot, current=start + timedelta(minutes=15 * i))
        message = bot.messages[-1]
        assert [item.data["qq"] for item in message if item.type == "at"] == [str(uid)]
        image = _png(message)
        assert image.height >= cards.REMINDER_MIN_HEIGHT > image.width
        assert image.getpixel(avatar_center) == colors[uid]


def test_personal_reminder_draws_one_template_for_all_pages(offline, monkeypatch):
    if not cards._font_path(config.hoyo_events_card_font):
        pytest.skip("系统没有可用的中文字体")
    backgrounds = cards.REMINDER_BACKGROUNDS
    assert len(backgrounds) == 4
    picks = iter(backgrounds)
    monkeypatch.setattr(cards, "choice", lambda pool: next(picks))
    monkeypatch.setattr(cards, "REMINDER_PAGE_SIZE", 2)
    view = service.build_view()

    for path in backgrounds:
        with Image.open(path) as source:
            expected = source.resize((cards.WIDTH, cards.REMINDER_MIN_HEIGHT)).getpixel((0, 0))
        pages = cards.render_personal_reminder_cards(view, "小明", font=config.hoyo_events_card_font)
        assert len(pages) == 2
        for page in pages:
            with Image.open(BytesIO(page)) as image:
                assert image.getpixel((0, 0)) == expected


@pytest.mark.asyncio
async def test_push_falls_back_to_text_when_chinese_font_is_missing(bot, offline, monkeypatch):
    monkeypatch.setattr(config, "hoyo_events_cards", True)
    monkeypatch.setattr(cards, "_font_path", lambda _: None)
    monkeypatch.setattr(messaging, "_fetch_qq_avatar", _no_avatar)
    store.touch_player("g888", 100)
    assert await jobs.push_group_once(888, bot)
    message = bot.messages[-1]
    assert message[0].type == "text"
    assert "幽境危战" in message.extract_plain_text()
    assert [item.data["qq"] for item in message if item.type == "at"] == ["100"]
