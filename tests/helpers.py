from __future__ import annotations

from datetime import timedelta

import nonebot
from nonebot.adapters.onebot.v11 import Adapter, Bot, Message
from nonebot.adapters.onebot.v11.event import GroupMessageEvent, PrivateMessageEvent, Sender

from nonebot_plugin_hoyo_events.models import iso, now

SELF_ID = "10001"


class FakeBot(Bot):
    """不发真实请求的 OneBot bot，所有 API 调用都被记录下来。"""

    def __init__(self, adapter) -> None:
        super().__init__(adapter, SELF_ID)
        self.sent: list = []

    async def call_api(self, api: str, **data):  # noqa: ANN003
        self.sent.append((api, data))
        return {"message_id": 1000 + len(self.sent)}

    @property
    def messages(self) -> list:
        return [
            data["message"] for _, data in self.sent if isinstance(data.get("message"), Message)
        ]

    @property
    def last_text(self) -> str:
        messages = self.messages
        return messages[-1].extract_plain_text() if messages else ""


def make_bot() -> FakeBot:
    return FakeBot(Adapter(nonebot.get_driver()))


def group_event(text: str, user_id: int = 100, group_id: int = 888, role: str = "member"):
    return GroupMessageEvent(
        time=int(now().timestamp()),
        self_id=int(SELF_ID),
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=1,
        message=Message(text),
        original_message=Message(text),
        raw_message=text,
        font=0,
        sender=Sender(user_id=user_id, nickname=f"用户{user_id}", role=role),
        to_me=True,
        group_id=group_id,
    )


def private_event(text: str, user_id: int = 100):
    return PrivateMessageEvent(
        time=int(now().timestamp()),
        self_id=int(SELF_ID),
        post_type="message",
        sub_type="friend",
        user_id=user_id,
        message_type="private",
        message_id=1,
        message=Message(text),
        original_message=Message(text),
        raw_message=text,
        font=0,
        sender=Sender(user_id=user_id, nickname=f"用户{user_id}"),
        to_me=True,
    )


def activity_dict(name: str, game: str, uid: str, end_days: int, start_days: int = -3) -> dict:
    at = now()
    return {
        "uid": uid,
        "game": game,
        "name": name,
        "start": iso(at + timedelta(days=start_days)),
        "end": iso(at + timedelta(days=end_days)),
        "description": "",
        "kind": "",
        "cover": "",
        "generation": 1,
    }


def seed_store(store) -> None:
    """写入三个游戏的假快照。"""

    at = now()
    store.set_snapshot(
        "ys",
        {
            "game": "ys",
            "version": "7.2",
            "version_name": "坏灭肇始之时",
            "start": iso(at - timedelta(days=5)),
            "end": iso(at + timedelta(days=26)),
            "fetched_at": iso(at),
            "activities": [
                activity_dict("幽境危战", "ys", "ys.a#1", 4),
                activity_dict("砺行修远", "ys", "ys.b#1", 40),
            ],
        },
    )
    store.set_snapshot(
        "sr",
        {
            "game": "sr",
            "version": "4.7",
            "version_name": "此刻，将群星付之一笑",
            "start": iso(at - timedelta(days=5)),
            "end": iso(at + timedelta(days=33)),
            "fetched_at": iso(at),
            "activities": [activity_dict("虚构叙事", "sr", "sr.a#1", 10)],
        },
    )
    store.set_snapshot("zzz", {"game": "zzz", "activities": [], "fetched_at": iso(at)})
