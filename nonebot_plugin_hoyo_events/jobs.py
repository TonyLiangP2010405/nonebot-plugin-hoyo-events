from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import nonebot
from nonebot.adapters.onebot.v11 import Bot

from .formatter import render_push
from .messaging import build_push_message
from .models import fmt_md, now, parse_datetime
from .runtime import default_push_time, service, store
from .service import build_scope_key

logger = logging.getLogger("nonebot_plugin_hoyo_events")

MAX_PUSH_ATTEMPTS = 5


def _bots() -> list:
    result = []
    for bot in nonebot.get_bots().values():
        if isinstance(bot, Bot):
            result.append(bot)
    return result


def build_group_push(group_id: int) -> Optional[object]:
    """构造某个群的推送消息，没有活动时返回 None。"""

    scope = build_scope_key(group_id)
    current = service.group_settings(group_id)
    view = service.build_view(None, scope, 0, group_id=group_id)
    if not view.entries:
        return None

    service.remember_view(scope, view)

    if current.at_style == "按活动":
        targets = service.at_targets(scope, view.activities(), group_id)
        return render_push(
            view,
            targets=targets,
            style="event",
            remind_days=current.remind_days,
            no_player_hint=not store.players(scope),
            max_at_lines=current.max_at_lines,
            max_at_users=current.max_at_users,
        )

    persons = service.person_statuses(scope, view, group_id)
    return render_push(
        view,
        persons=persons,
        style="person",
        remind_days=current.remind_days,
        no_player_hint=not store.players(scope),
        max_at_users=current.max_at_users,
    )


async def push_group_once(group_id: int, bot: Optional[Bot] = None) -> bool:
    """给单个群推送一次，返回是否成功。"""

    await service.startup()
    if not service.has_data():
        await service.ensure_data()
    payload = build_group_push(group_id)
    if payload is None:
        logger.info("群 %s 没有进行中的活动，跳过推送", group_id)
        return False

    message = build_push_message(payload)
    candidates = [bot] if bot else _bots()
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            await candidate.send_group_msg(group_id=group_id, message=message)
            logger.info("群 %s 活动推送成功", group_id)
            return True
        except Exception as exc:  # noqa: BLE001 - 单个 bot 失败不影响其他 bot
            logger.warning("群 %s 活动推送失败：%s", group_id, exc)
    return False


def group_push_time(record: dict) -> tuple:
    hour, minute = default_push_time()
    try:
        hour = int(record.get("hour", hour))
        minute = int(record.get("minute", minute))
    except (TypeError, ValueError):
        pass
    if not (0 <= hour <= 23):
        hour = default_push_time()[0]
    if not (0 <= minute <= 59):
        minute = default_push_time()[1]
    return hour, minute


async def push_due_groups(current: Optional[datetime] = None) -> list:
    """到点就推送；同一群一天只推一次。"""

    await service.startup()
    current = current or now()
    today = current.date().isoformat()
    pushed = []

    for group_key, record in list(store.all_groups().items()):
        if not isinstance(record, dict):
            continue
        try:
            group_id = int(group_key)
        except (TypeError, ValueError):
            continue

        current_settings = service.settings.group(group_id)
        if not current_settings.push_enabled:
            continue
        hour, minute = current_settings.hour, current_settings.minute
        if (current.hour, current.minute) < (hour, minute):
            continue

        if record.get("last_push") == today:
            continue
        if record.get("push_day") != today:
            record["push_day"] = today
            record["push_attempts"] = 0
        attempts = int(record.get("push_attempts") or 0)
        if attempts >= MAX_PUSH_ATTEMPTS:
            record["last_push"] = today
            continue

        record["push_attempts"] = attempts + 1
        if await push_group_once(group_id):
            record["last_push"] = today
            record["last_push_at"] = current.isoformat()
            pushed.append(group_id)
        elif record["push_attempts"] >= MAX_PUSH_ATTEMPTS:
            # 重试用完，今天不再尝试，避免刷屏
            record["last_push"] = today
            logger.warning("群 %s 连续 %s 次推送失败，今天放弃", group_id, MAX_PUSH_ATTEMPTS)
        else:
            logger.warning(
                "群 %s 第 %s 次推送失败（今天最多重试 %s 次）",
                group_id,
                attempts + 1,
                MAX_PUSH_ATTEMPTS,
            )

    if store.all_groups():
        await store.commit()
    return pushed


async def refresh_all(force: bool = True) -> dict:
    await service.startup()
    return await service.ensure_data(force=force)


async def refresh_if_due(current: Optional[datetime] = None) -> bool:
    """按「刷新分钟」设置决定这次要不要拉数据（设置改了立刻生效）。"""

    await service.startup()
    interval = service.settings.global_settings().refresh_interval
    if interval <= 0:
        return False
    current = current or now()
    last = parse_datetime(store.meta("last_refresh"))
    if last and (current - last).total_seconds() < interval * 60 - 5:
        return False
    await service.ensure_data(force=True)
    return True


def register_jobs(scheduler) -> None:  # noqa: ANN001 - apscheduler 实例
    @scheduler.scheduled_job(
        "interval",
        minutes=1,
        id="hoyo_events_tick",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=120,
    )
    async def _tick_job() -> None:
        try:
            await refresh_if_due()
        except Exception as exc:  # noqa: BLE001
            logger.warning("定时刷新活动数据失败：%s", exc)
        try:
            await push_due_groups()
        except Exception as exc:  # noqa: BLE001
            logger.warning("活动推送任务异常：%s", exc)


__all__ = [
    "build_group_push",
    "push_due_groups",
    "push_group_once",
    "refresh_all",
    "register_jobs",
    "fmt_md",
]
