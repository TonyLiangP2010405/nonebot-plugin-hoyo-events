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
from pathlib import Path

os.environ.setdefault("HOYO_EVENTS_DATA_DIR", tempfile.mkdtemp(prefix="hoyo-demo-"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot  # noqa: E402

nonebot.init(driver="~none", command_start={""})
nonebot.load_plugin("nonebot_plugin_hoyo_events")

from nonebot_plugin_hoyo_events.formatter import (  # noqa: E402
    render_completion,
    render_target_line,
    render_view,
)
from nonebot_plugin_hoyo_events.jobs import build_group_push  # noqa: E402
from nonebot_plugin_hoyo_events.models import fmt_md, now  # noqa: E402
from nonebot_plugin_hoyo_events.runtime import service, store  # noqa: E402
from nonebot_plugin_hoyo_events.service import (  # noqa: E402
    build_scope_key,
    build_view_key,
)

GROUP = 123456
SCOPE = build_scope_key(GROUP)
USERS = {1001: "小明", 1002: "小红", 1003: "阿伟"}


def show(title: str) -> None:
    print(f"\n{'=' * 12} {title} {'=' * 12}")


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

    show("次日 21:00 自动推送（@ 未完成的人）")
    payload = build_group_push(GROUP)
    if payload is None:
        print("没有进行中的活动")
        return
    print(payload.head)
    print()
    for target in payload.targets:
        names = " ".join(f"@{USERS.get(uid, uid)}" for uid in target.users)
        print(render_target_line(target) + " " + names)
    print(payload.tail)


if __name__ == "__main__":
    asyncio.run(main())
