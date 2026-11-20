from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import nonebot
from nonebot.adapters.onebot.v11 import Bot

from .messaging import build_personal_reminder_message
from .models import Entry, View, now, parse_datetime
from .runtime import service, store
from .service import build_scope_key, build_view_key

logger = logging.getLogger("nonebot_plugin_hoyo_events")

MAX_PUSH_ATTEMPTS = 5
REMINDER_INTERVAL = timedelta(minutes=15)
_push_lock = asyncio.Lock()


def _bots() -> list:
    result = []
    for bot in nonebot.get_bots().values():
        if isinstance(bot, Bot):
            result.append(bot)
    return result


def _reminder_queue(group_id: int, active_ids: set[str], state: dict) -> list[tuple[int, set[str]]]:
    scope = build_scope_key(group_id)
    muted = store.muted(group_id)
    sent = state["sent"]
    attempts = state["attempts"]
    queue = []
    for key in store.players(scope):
        if not key.isdecimal():
            continue
        uid = int(key)
        if uid in muted or attempts.get(key, 0) >= MAX_PUSH_ATTEMPTS:
            continue
        pending = active_ids - store.done_uids(scope, uid)
        if pending.difference(sent.get(key, [])):
            queue.append((uid, pending))
    last_user = state.get("last_user", 0)
    # 从上一次提醒过的人后面接着排，跨天积压时也能轮到后面的成员。
    queue.sort(key=lambda item: (item[0] <= last_user, item[0]))
    return queue


async def push_group_once(
    group_id: int,
    bot: Optional[Bot] = None,
    *,
    current: Optional[datetime] = None,
    scheduled: bool = False,
) -> bool:
    """发送队列中的下一张个人卡片。群内两次成功提醒至少间隔 15 分钟。"""

    await service.startup()
    candidates = [bot] if bot else _bots()
    if not candidates:
        return False
    if not scheduled and not service.has_data():
        await service.ensure_data()

    async with _push_lock:
        at = current or now()
        settings = service.group_settings(group_id)
        start = at.replace(hour=settings.hour, minute=settings.minute, second=0, microsecond=0)
        day = (start if at >= start else start - timedelta(days=1)).date().isoformat()
        state = store.group(group_id).get("personal_reminders") or {}
        if scheduled and (not settings.push_enabled or (at < start and state.get("day") != day)):
            return False
        last_sent = parse_datetime(state.get("last_sent"))
        if last_sent and at - last_sent < REMINDER_INTERVAL:
            return False

        new_round = state.get("day") != day
        if new_round:
            state.update(day=day, sent={}, attempts={})
            store.update_group(group_id, personal_reminders=state)

        activities = service.active_activities(at=at)
        active_ids = {activity.uid for activity in activities}
        queue = _reminder_queue(group_id, active_ids, state)
        if not queue:
            if new_round:
                await store.commit()
            return False

        uid, pending = queue[0]
        scope = build_scope_key(group_id)
        record = store.player(scope, uid)
        view = View(at, entries=[
            Entry(index, activity, done=activity.uid not in pending)
            for index, activity in enumerate(activities, start=1)
        ])
        message = await build_personal_reminder_message(
            view, record.get("nick") or str(uid), uid, len(queue) > 1
        )
        # 渲染期间用户可能已经做完、静音、退订或关闭群提醒。
        if (
            not store.player(scope, uid)
            or uid in store.muted(group_id)
            or pending != active_ids - store.done_uids(scope, uid)
            or (scheduled and not service.group_settings(group_id).push_enabled)
        ):
            if new_round:
                await store.commit()
            return False

        key = str(uid)
        for candidate in candidates:
            try:
                await candidate.send_group_msg(group_id=group_id, message=message)
            except Exception as exc:
                logger.warning("群 %s 给 %s 的活动提醒发送失败：%s", group_id, uid, exc)
                continue
            state["last_sent"] = (current or now()).isoformat()
            state["last_user"] = uid
            state["sent"][key] = sorted(pending.union(state["sent"].get(key, [])))
            state["attempts"].pop(key, None)
            view_key = build_view_key(group_id, uid)
            latest = service.get_view(view_key)
            if latest is None or latest.at <= view.at:
                service.remember_view(view_key, view)
            await store.commit()
            return True

        attempts = state["attempts"]
        attempts[key] = attempts.get(key, 0) + 1
        await store.commit()
        return False


async def push_due_groups(current: Optional[datetime] = None) -> list:
    """检查各群，每次最多给每个群发一张个人提醒。"""

    await service.startup()
    pushed = []
    for key in list(store.all_groups()):
        if key.isdecimal() and await push_group_once(int(key), current=current, scheduled=True):
            pushed.append(int(key))
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
        id="hoyo_events_refresh",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=120,
    )
    async def _refresh_job() -> None:
        try:
            await refresh_if_due()
        except Exception as exc:  # noqa: BLE001
            logger.warning("定时刷新活动数据失败：%s", exc)

    @scheduler.scheduled_job(
        "interval",
        seconds=10,
        id="hoyo_events_reminders",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=120,
    )
    async def _reminder_job() -> None:
        try:
            await push_due_groups()
        except Exception as exc:  # noqa: BLE001
            logger.warning("活动推送任务异常：%s", exc)


__all__ = [
    "push_due_groups",
    "push_group_once",
    "refresh_all",
    "register_jobs",
]
