from __future__ import annotations

from typing import Iterable, Optional

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .formatter import PushMessage, render_target_line


def plain_message(text: str) -> Message:
    return Message(MessageSegment.text(text))


def build_push_message(payload: PushMessage) -> Message:
    """把标题、@ 名单、结尾拼成一条 OneBot 消息。"""

    message = Message(MessageSegment.text(payload.head))

    if payload.targets:
        for target in payload.targets:
            message += MessageSegment.text("\n" + render_target_line(target))
            for user_id in target.users:
                message += MessageSegment.at(user_id)
                message += MessageSegment.text(" ")

    if payload.overflow:
        count, users = payload.overflow
        message += MessageSegment.text(f"\n· 其余 {count} 个活动：")
        for user_id in users:
            message += MessageSegment.at(user_id)
            message += MessageSegment.text(" ")

    if payload.tail:
        message += MessageSegment.text("\n" + payload.tail)

    return message


def at_list(user_ids: Iterable[int]) -> str:
    """纯文本场景下把用户列表渲染成 CQ 吗（备用）。"""

    return "".join(MessageSegment.at(int(uid)) for uid in user_ids)


def safe_text(text: Optional[str], fallback: str = "") -> str:
    return text if text else fallback


__all__ = ["at_list", "build_push_message", "plain_message", "safe_text"]
