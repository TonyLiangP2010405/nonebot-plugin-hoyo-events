from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from typing import Optional, TypeVar

import httpx
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .cards import render_activity_card, render_personal_reminder_cards, render_text_card
from .formatter import REMINDER_FOOTER, personal_reminder_row, render_view
from .models import View, fmt_md
from .runtime import config

logger = logging.getLogger("nonebot_plugin_hoyo_events")
T = TypeVar("T")
QQ_AVATAR_URL = "https://q1.qlogo.cn/g"
MAX_AVATAR_BYTES = 512 * 1024


def plain_message(text: str) -> Message:
    return Message(MessageSegment.text(text))


def append_lines(message: Message, lines: list) -> Message:
    """把 @ 小节拼到消息里。"""

    for line in lines:
        message += MessageSegment.text("\n" + line.prefix)
        for user_id in line.users:
            message += MessageSegment.at(user_id)
            message += MessageSegment.text(" ")
        if line.suffix:
            message += MessageSegment.text(line.suffix)
    return message


async def _render_card(renderer: Callable[..., T], *args) -> T | None:
    if not config.hoyo_events_cards:
        return None
    try:
        return await asyncio.to_thread(renderer, *args, font=config.hoyo_events_card_font)
    except Exception as exc:
        logger.warning("渲染活动卡片失败，使用文字消息：%s", exc)
        return None


async def _card_segment(renderer: Callable[..., bytes], *args) -> MessageSegment | None:
    png = await _render_card(renderer, *args)
    return MessageSegment.image(png) if png else None


async def build_text_card_message(text: str) -> Message:
    image = await _card_segment(render_text_card, text)
    return Message(image) if image else plain_message(text)


async def build_activity_message(
    view: View, title: str, remind_days: int, footer: str, progress: str = ""
) -> Message:
    image = await _card_segment(render_activity_card, view, title, remind_days, footer, progress)
    if image:
        return Message(image)
    suffix = f"（{progress}）" if progress else f" · {fmt_md(view.at)}"
    return plain_message(render_view(view, remind_days, title=title + suffix, footer=footer))


async def _download_qq_avatar(user_id: int) -> bytes | None:
    params = {"b": "qq", "nk": str(user_id), "s": "100"}
    async with httpx.AsyncClient(timeout=3) as client, client.stream(
        "GET", QQ_AVATAR_URL, params=params
    ) as response:
        response.raise_for_status()
        if not response.headers.get("Content-Type", "").lower().startswith("image/"):
            return None
        data = bytearray()
        async for chunk in response.aiter_bytes():
            if len(data) + len(chunk) > MAX_AVATAR_BYTES:
                return None
            data.extend(chunk)
        return bytes(data)


async def _fetch_qq_avatar(user_id: int) -> bytes | None:
    try:
        return await asyncio.wait_for(_download_qq_avatar(user_id), timeout=3)
    except (asyncio.TimeoutError, httpx.HTTPError):
        return None


async def build_personal_reminder_message(
    view: View, nick: str, user_id: int, has_next: bool
) -> Message:
    avatar = await _fetch_qq_avatar(user_id) if config.hoyo_events_cards else None
    images = await _render_card(render_personal_reminder_cards, view, nick, avatar)
    if images:
        message = Message([MessageSegment.image(image) for image in images])
    else:
        lines = [f"{nick}的活动进度"]
        for done, title in ((False, "未完成"), (True, "已完成")):
            rows = [personal_reminder_row(entry, view.at) for entry in view.entries if entry.done == done]
            lines.extend([f"\n{title}（{len(rows)} 项）", *rows])
        message = plain_message("\n".join([*lines, REMINDER_FOOTER]))
    message += MessageSegment.text("\n")
    message += MessageSegment.at(user_id)
    if has_next:
        message += MessageSegment.text(" 15 分钟后提醒下一位。")
    return message


def at_list(user_ids: Iterable[int]) -> str:
    """纯文本场景下把用户列表渲染成 CQ 吗（备用）。"""

    return "".join(MessageSegment.at(int(uid)) for uid in user_ids)


def safe_text(text: Optional[str], fallback: str = "") -> str:
    return text if text else fallback


__all__ = [
    "append_lines",
    "at_list",
    "build_activity_message",
    "build_personal_reminder_message",
    "build_text_card_message",
    "plain_message",
    "safe_text",
]
