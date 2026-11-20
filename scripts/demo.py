#!/usr/bin/env python
"""本地演示脚本：拉真实活动数据，模拟一次群聊交互。

用法::

    python scripts/demo.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

os.environ.setdefault("HOYO_EVENTS_DATA_DIR", tempfile.mkdtemp(prefix="hoyo-demo-"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot  # noqa: E402
from nonebot.adapters.onebot.v11 import Adapter, Bot  # noqa: E402

nonebot.init(driver="~none", command_start={""})
nonebot.load_plugin("nonebot_plugin_hoyo_events")

from nonebot_plugin_hoyo_events.formatter import (  # noqa: E402
    render_completion,
    render_view,
)
from nonebot_plugin_hoyo_events.jobs import push_group_once  # noqa: E402
from nonebot_plugin_hoyo_events.models import fmt_md, now  # noqa: E402
from nonebot_plugin_hoyo_events.runtime import config, service, store  # noqa: E402
from nonebot_plugin_hoyo_events.service import (  # noqa: E402
    build_scope_key,
    build_view_key,
)

GROUP = 123456
SCOPE = build_scope_key(GROUP)
USERS = {1001: "小明", 1002: "小红", 1003: "阿伟"}


def show(title: str) -> None:
    print(f"\n{'=' * 12} {title} {'=' * 12}")


class DemoBot(Bot):
    async def send_group_msg(self, *, group_id, message):
        for segment in message:
            if segment.type == "at":
                uid = int(segment.data["qq"])
                print(f"@{USERS.get(uid, uid)}", end="")
            else:
                print(str(segment), end="")
        print()


async def main() -> None:
    results = await service.ensure_data(force=True)
    show("刷新数据")
    print(service.refresh_summary(results))

    for uid, nick in USERS.items():
        store.touch_player(SCOPE, uid, nick)

    show("群友发「/活动」")
    view = service.build_view(None, SCOPE, 1001)
    service.remember_view(build_view_key(GROUP, 1001), view)
    service.remember_view(SCOPE, view)
    print(render_view(view, 3, title=f"🎮 米哈游活动 · {fmt_md(now())}"))

    show("小明发「/已完成 1 3」")
    outcome = await service.mark_completed(
        SCOPE,
        1001,
        ["1", "3"],
        nick="小明",
        view_keys=(build_view_key(GROUP, 1001), SCOPE),
    )
    pending = len(service.pending(SCOPE, 1001))
    total = len(service.active_activities())
    print(render_completion(outcome, pending, total))

    show("模拟群内逐人提醒（文字版）")
    config.hoyo_events_cards = False
    bot = DemoBot(Adapter(nonebot.get_driver()), "10000")
    start = now().replace(hour=21, minute=0, second=0, microsecond=0)
    for index in range(len(USERS)):
        if not await push_group_once(GROUP, bot, current=start + timedelta(minutes=15 * index)):
            break


if __name__ == "__main__":
    asyncio.run(main())
