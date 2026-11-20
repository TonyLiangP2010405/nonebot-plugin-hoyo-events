from __future__ import annotations

from typing import Iterable, Optional

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .formatter import PushMessage


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


def build_push_message(payload: PushMessage) -> Message:
    """把正文、@ 名单、结尾拼成一条 OneBot 消息。"""

    message = Message(MessageSegment.text(payload.head))
    append_lines(message, payload.lines)
    if payload.tail:
        message += MessageSegment.text("\n" + payload.tail)
    return message


def at_list(user_ids: Iterable[int]) -> str:
    """纯文本场景下把用户列表渲染成 CQ 吗（备用）。"""

    return "".join(MessageSegment.at(int(uid)) for uid in user_ids)


def safe_text(text: Optional[str], fallback: str = "") -> str:
    return text if text else fallback


__all__ = ["append_lines", "at_list", "build_push_message", "plain_message", "safe_text"]
