from __future__ import annotations

import asyncio
import logging

from nonebot import get_driver, require
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="米哈游活动助手",
    description="原神 / 崩坏：星穹铁道 / 绝区零 活动日历、剩余天数与未完成成员 @ 提醒",
    usage=(
        "/活动 —— 三个游戏的活动总览（含剩余天数）\n"
        "/原神活动 /星铁活动 /绝区零活动 —— 单游戏活动\n"
        "/已完成 1 3 —— 标记列表里的第 1、3 项（按人、按群、按活动分别记录）\n"
        "/已完成 活动名 —— 按名称标记，支持模糊匹配\n"
        "/未完成 —— 查看自己还差哪些活动\n"
        "/取消完成 1 —— 撤销标记\n"
        "/活动提醒 开|关|时间 21:30|状态|立即 —— 群管设置每日推送\n"
        "/活动免打扰 —— 不再被 @\n"
        "/活动刷新 —— 管理员手动拉取最新活动\n"
        "/活动帮助 —— 查看完整说明"
    ),
    type="application",
    homepage="https://github.com/TonyLiangP2010405/nonebot-plugin-hoyo-events",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

require("nonebot_plugin_apscheduler")
require("nonebot_plugin_localstore")

from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from . import jobs  # noqa: E402
from .commands import (  # noqa: E402,F401
    done_matcher,
    game_matcher,
    help_matcher,
    list_matcher,
    mute_matcher,
    push_matcher,
    refresh_matcher,
    todo_matcher,
    undo_matcher,
)
from .runtime import DATA_DIR, config, service  # noqa: E402

logger = logging.getLogger("nonebot_plugin_hoyo_events")

if config.hoyo_events_enabled:
    jobs.register_jobs(scheduler)

    driver = get_driver()

    @driver.on_startup
    async def _startup() -> None:
        """启动时加载数据并异步拉一次活动，不阻塞 NoneBot 启动。"""

        await service.startup()

        async def _warm_up() -> None:
            try:
                await service.ensure_data()
            except Exception as exc:  # noqa: BLE001
                logger.warning("启动时拉取活动数据失败：%s", exc)

        asyncio.create_task(_warm_up())

    logger.info("米哈游活动助手已加载，数据目录：%s", DATA_DIR)
