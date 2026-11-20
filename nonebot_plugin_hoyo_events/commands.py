from __future__ import annotations

import logging
import re

from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.params import CommandArg

from .formatter import (
    render_completion,
    render_help,
    render_status,
    render_target_line,
    render_view,
    render_watch_list,
)
from .games import GAME_BY_KEY, resolve_game
from .jobs import group_push_time, push_group_once
from .messaging import plain_message
from .models import fmt_md
from .runtime import config, default_push_time, parse_push_time, service, store
from .service import build_scope_key, build_view_key, scope_of_private

logger = logging.getLogger("nonebot_plugin_hoyo_events")

QUEUE = "活动"

admin_only = "只有群主/管理员或超级用户才能修改提醒设置哦"
superuser_only = "这是管理员指令哦"


# --------------------------------------------------------------------------- 工具函数
def _split_tokens(text: str) -> list:
    return [item for item in re.split(r"[\s,，、;；/|]+", (text or "").strip()) if item]


def _is_superuser(event: MessageEvent) -> bool:
    superusers = getattr(get_driver().config, "superusers", set()) or set()
    return str(event.get_user_id()) in {str(item) for item in superusers}


def _is_group_admin(event: MessageEvent) -> bool:
    if _is_superuser(event):
        return True
    sender = getattr(event, "sender", None)
    role = getattr(sender, "role", "") if sender else ""
    return role in ("owner", "admin")


def _nick(event: MessageEvent) -> str:
    sender = getattr(event, "sender", None)
    if not sender:
        return ""
    return getattr(sender, "card", "") or getattr(sender, "nickname", "") or ""


def _context(event: MessageEvent) -> tuple:
    """返回 (scope, group_id)。group_id 为 None 表示私聊。"""

    if isinstance(event, GroupMessageEvent):
        return build_scope_key(event.group_id), event.group_id
    return scope_of_private(int(event.get_user_id())), None


MAX_WATCH_PER_COMMAND = 10


def _mentioned_ids(event: MessageEvent) -> list:
    """取出消息里 @ 到的人（忽略 @全体成员）。"""

    result = []
    for segment in event.get_message():
        if segment.type != "at":
            continue
        qq = str(segment.data.get("qq", ""))
        if not qq.isdigit():
            continue
        uid = int(qq)
        if uid not in result:
            result.append(uid)
    return result


async def _member_nick(bot: Bot, group_id: int, user_id: int) -> str:
    """尽力拿群名片，拿不到就用 QQ 号占位。"""

    try:
        info = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
    except Exception:  # noqa: BLE001 - 协议端差异很大，拿不到不算错
        return ""
    if isinstance(info, dict):
        return str(info.get("card") or info.get("nickname") or "")
    return ""


def _display_name(nick: str, user_id: int) -> str:
    return nick or str(user_id)


def _can_manage_push(event: MessageEvent) -> bool:
    return _is_group_admin(event) or not config.hoyo_events_push_admin_only


def _group_only_hint() -> str:
    return "这个功能需要在群里使用。私聊可以直接发「活动」查看活动列表。"


async def _prepare() -> None:
    """确保数据已加载/刷新，并吞掉网络异常。"""

    await service.ensure_data()


# --------------------------------------------------------------------------- 帮助
help_matcher = on_command(
    "活动帮助",
    aliases={"活动说明", "hoyo帮助", "活动菜单"},
    priority=5,
    block=True,
)


@help_matcher.handle()
async def _handle_help() -> None:
    push_time = f"{default_push_time()[0]:02d}:{default_push_time()[1]:02d}"
    await help_matcher.finish(render_help(push_time, config.hoyo_events_remind_days))


# --------------------------------------------------------------------------- 查询活动
async def _send_list(matcher, event: MessageEvent, text: str) -> None:
    await _prepare()

    game = None
    force_at = config.hoyo_events_list_at
    for token in _split_tokens(text):
        if token.lower() in {"提醒", "@", "at", "艾特", "ping"}:
            force_at = True
            continue
        if token.lower() in {"全部", "所有", "全部活动"}:
            game = None
            continue
        maybe = resolve_game(token) if len(token) <= 10 else None
        if maybe:
            game = maybe.key

    if not service.has_data():
        await matcher.finish(
            "😵 暂时拿不到活动数据（数据源可能挂了），稍后再试试吧。\n"
            "如果长期这样，可以在数据目录的 override.json 里手动补活动。"
        )

    scope, group_id = _context(event)
    user_id = int(event.get_user_id())
    store.touch_player(scope, user_id, _nick(event))
    if group_id and config.hoyo_events_push_default_enabled and not store.group(group_id):
        hour, minute = default_push_time()
        store.update_group(group_id, enabled=True, hour=hour, minute=minute)

    view = service.build_view(game, scope, user_id)
    service.remember_view(build_view_key(group_id, user_id), view)
    await store.commit()

    if not view.entries:
        await matcher.finish("🎉 当前没有进行中的活动。\n最近要开的活动会在每日推送里出现。")

    if game:
        meta = GAME_BY_KEY[game]
        title = f"{meta.emoji}{meta.name} 活动 · {fmt_md(view.at)}"
    else:
        title = f"🎮 米哈游活动 · {fmt_md(view.at)}"

    body = render_view(
        view,
        remind_days=config.hoyo_events_remind_days,
        title=title,
        footer="发送「已完成 编号」标记完成，如：已完成 1 3",
    )

    message = plain_message(body)
    if force_at and group_id:
        targets = service.at_targets(scope, view.activities(), group_id)
        if targets:
            message = Message(MessageSegment.text(body))
            message += MessageSegment.text("\n\n⚠️ 还没完成的群友：")
            for target in targets:
                message += MessageSegment.text("\n" + render_target_line(target))
                for uid in target.users:
                    message += MessageSegment.at(uid)
                    message += MessageSegment.text(" ")

    await matcher.send(message)


list_matcher = on_command(
    "活动",
    aliases={"活动列表", "活动日历", "游戏活动", "今日活动", "活动查询"},
    priority=10,
    block=True,
)


@list_matcher.handle()
async def _handle_list(bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    await _send_list(list_matcher, event, args.extract_plain_text())


game_matcher = on_command(
    "原神活动",
    aliases={"星铁活动", "星穹铁道活动", "崩铁活动", "绝区零活动", "绝区活动"},
    priority=11,
    block=True,
)


@game_matcher.handle()
async def _handle_game_list(bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    plain = event.get_plaintext().strip().lstrip("/!！／~～")
    token = re.split(r"[\s,，、]+", plain)[0] if plain else ""
    name = token.replace("活动", "") or token
    game = resolve_game(name)
    extra = args.extract_plain_text()
    await _send_list(game_matcher, event, f"{game.key if game else ''} {extra}".strip())


# --------------------------------------------------------------------------- 已完成
done_matcher = on_command(
    "已完成",
    aliases={"我已完成", "活动完成", "完成活动"},
    priority=10,
    block=True,
)


@done_matcher.handle()
async def _handle_done(event: MessageEvent, args: Message = CommandArg()) -> None:
    scope, group_id = _context(event)
    user_id = int(event.get_user_id())
    nick = _nick(event)
    await _prepare()

    tokens = _split_tokens(args.extract_plain_text())
    view_keys = (build_view_key(group_id, user_id), build_scope_key(group_id))

    if not tokens:
        view = service.build_view(None, scope, user_id, only_pending=True)
        if not view.entries:
            await done_matcher.finish("🎉 你没有未完成的活动，不会再 @ 你啦。")
        service.remember_view(build_view_key(group_id, user_id), view)
        await store.commit()
        await done_matcher.finish(
            render_view(
                view,
                remind_days=config.hoyo_events_remind_days,
                title="📌 你还没完成的活动",
                footer="发送「已完成 编号」标记，例如：已完成 1 3\n也可以「已完成 全部」一键完成。",
            )
        )

    if not service.has_data():
        await done_matcher.finish("😵 暂时拿不到活动数据，晚点再试试。")

    outcome = await service.mark_completed(scope, user_id, tokens, nick=nick, view_keys=view_keys)
    pending = len(service.pending(scope, user_id))
    total = len(service.active_activities())
    await done_matcher.finish(render_completion(outcome, pending, total))


# --------------------------------------------------------------------------- 未完成
todo_matcher = on_command(
    "未完成",
    aliases={"活动进度", "我的活动", "活动待办"},
    priority=10,
    block=True,
)


@todo_matcher.handle()
async def _handle_todo(event: MessageEvent) -> None:
    scope, group_id = _context(event)
    user_id = int(event.get_user_id())
    await _prepare()

    view = service.build_view(None, scope, user_id, only_pending=True)
    if not view.entries:
        await todo_matcher.finish("🎉 当前没有未完成的活动，全部搞定啦！")
    service.remember_view(build_view_key(group_id, user_id), view)
    await store.commit()

    done_count, total = service.done_count(scope, user_id)
    await todo_matcher.finish(
        render_view(
            view,
            remind_days=config.hoyo_events_remind_days,
            title=f"📌 你还没完成的活动（{done_count}/{total}）",
            footer="发送「已完成 编号」标记，例如：已完成 1 3",
        )
    )


# --------------------------------------------------------------------------- 取消完成
undo_matcher = on_command(
    "取消完成",
    aliases={"撤销完成", "取消已完成", "标记未完成"},
    priority=10,
    block=True,
)


@undo_matcher.handle()
async def _handle_undo(event: MessageEvent, args: Message = CommandArg()) -> None:
    scope, group_id = _context(event)
    user_id = int(event.get_user_id())
    await _prepare()

    tokens = _split_tokens(args.extract_plain_text())
    if not tokens:
        await undo_matcher.finish("用法：取消完成 编号 / 活动名，例如「取消完成 1」")

    view_keys = (build_view_key(group_id, user_id), build_scope_key(group_id))
    outcome = await service.unmark_completed(scope, user_id, tokens, view_keys=view_keys)
    pending = len(service.pending(scope, user_id))
    _, total = service.done_count(scope, user_id)
    await undo_matcher.finish(render_completion(outcome, pending, total))


# --------------------------------------------------------------------------- 免打扰
mute_matcher = on_command(
    "活动免打扰",
    aliases={"免打扰", "活动静音"},
    priority=10,
    block=True,
)


@mute_matcher.handle()
async def _handle_mute(event: MessageEvent, args: Message = CommandArg()) -> None:
    if not isinstance(event, GroupMessageEvent):
        await mute_matcher.finish(_group_only_hint())

    user_id = int(event.get_user_id())
    group_id = event.group_id
    tokens = _split_tokens(args.extract_plain_text())
    muted = store.muted(group_id)

    if tokens and tokens[0] in {"开", "开启", "on"}:
        store.mute(group_id, user_id)
        await store.commit()
        await mute_matcher.finish("已开启免打扰，之后不会再 @ 你（活动列表里仍会显示你的进度）。")
    if tokens and tokens[0] in {"关", "关闭", "off"}:
        store.unmute(group_id, user_id)
        await store.commit()
        await mute_matcher.finish("已关闭免打扰，未完成时会被 @ 提醒。")

    if user_id in muted:
        store.unmute(group_id, user_id)
        await store.commit()
        await mute_matcher.finish("已关闭免打扰，未完成时会被 @ 提醒。")
    store.mute(group_id, user_id)
    await store.commit()
    await mute_matcher.finish("已开启免打扰，之后不会再 @ 你。")


# --------------------------------------------------------------------------- 提醒名单
watch_matcher = on_command(
    "活动关注",
    aliases={"报名活动", "活动报名", "关注活动"},
    priority=10,
    block=True,
)


@watch_matcher.handle()
async def _handle_watch(bot: Bot, event: MessageEvent) -> None:
    """把自己或 @ 到的人加进本群提醒名单，不需要群管权限。"""

    if not isinstance(event, GroupMessageEvent):
        await watch_matcher.finish(_group_only_hint())

    scope, group_id = _context(event)
    actor = int(event.get_user_id())
    await service.startup()

    mentions = _mentioned_ids(event)
    if len(mentions) > MAX_WATCH_PER_COMMAND:
        await watch_matcher.finish(f"一次最多 @ {MAX_WATCH_PER_COMMAND} 个人哦，分批来。")

    # 发起人默认也一起进名单；@ 到的人依次追加
    targets = [actor] + [uid for uid in mentions if uid != actor]
    others = [uid for uid in targets if uid != actor]
    if others and not config.hoyo_events_allow_add_others:
        await watch_matcher.finish("这个群只允许关注自己：直接发「活动关注」就行。")

    current = store.player_count(scope)
    fresh = [uid for uid in targets if not store.player(scope, uid)]
    if current + len(fresh) > config.hoyo_events_max_watch:
        await watch_matcher.finish(
            f"本群提醒名单已满（{current}/{config.hoyo_events_max_watch} 人），"
            "先发动「活动取关 @某人」腾个位置吧。"
        )

    added: list = []
    existed: list = []
    for uid in targets:
        nick = _nick(event) if uid == actor else await _member_nick(bot, group_id, uid)
        if store.add_player(scope, uid, nick, added_by=actor):
            added.append((uid, nick))
        else:
            existed.append((uid, nick or store.player(scope, uid).get("nick", "")))
    await store.commit()

    total = store.player_count(scope)
    lines = []
    if added:
        lines.append(f"✅ 已加入提醒名单 {len(added)} 人（本群共 {total} 人）")
    if existed:
        names = "、".join(_display_name(nick, uid) for uid, nick in existed)
        lines.append(f"· 之前就在名单里：{names}")
    if not added and not existed:
        lines.append("· 名单没有变化")

    message = Message(MessageSegment.text("\n".join(lines)))
    if added:
        message += MessageSegment.text("\n")
        for uid, nick in added:
            message += MessageSegment.text(f"· {_display_name(nick, uid)} ")
            if uid != actor:  # 自己不用再 @ 一遍
                message += MessageSegment.at(uid)
            message += MessageSegment.text("\n")
    message += MessageSegment.text(
        "以后有没做完的活动会 @ 他们，做完的不会再 @。\n发送「活动名单」查看全部。"
    )
    await watch_matcher.send(message)


unwatch_matcher = on_command(
    "活动取关",
    aliases={"取消关注", "退订活动", "活动退订"},
    priority=10,
    block=True,
)


@unwatch_matcher.handle()
async def _handle_unwatch(event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        await unwatch_matcher.finish(_group_only_hint())

    scope, group_id = _context(event)
    actor = int(event.get_user_id())
    await service.startup()

    mentions = _mentioned_ids(event)
    targets = mentions or [actor]
    if len(targets) > MAX_WATCH_PER_COMMAND:
        await unwatch_matcher.finish(f"一次最多 @ {MAX_WATCH_PER_COMMAND} 个人哦。")

    removed: list = []
    denied: list = []
    absent: list = []
    for uid in targets:
        record = store.player(scope, uid)
        if not record:
            absent.append(uid)
            continue
        if uid != actor:
            added_by = record.get("added_by")
            # 群管可以移除任何人；其他人只能移除「自己当初加进来的人」
            if not (_is_group_admin(event) or str(added_by) == str(actor)):
                denied.append((uid, record.get("nick", "")))
                continue
        store.remove_player(scope, uid)
        removed.append((uid, record.get("nick", "")))
    await store.commit()

    lines = []
    if removed:
        names = "、".join(_display_name(nick, uid) for uid, nick in removed)
        lines.append(f"✅ 已移出提醒名单：{names}")
    if denied:
        names = "、".join(_display_name(nick, uid) for uid, nick in denied)
        lines.append(f"⚠️ 没权限移出别人：{names}（只有群管或当初加他的人可以）")
    if absent:
        lines.append("· 这些人本来就不在名单里")
    if not lines:
        lines.append("· 名单没有变化")
    lines.append(f"本群当前共 {store.player_count(scope)} 人在名单里。")
    await unwatch_matcher.finish("\n".join(lines))


watchlist_matcher = on_command(
    "活动名单",
    aliases={"关注名单", "活动成员", "提醒名单"},
    priority=10,
    block=True,
)


@watchlist_matcher.handle()
async def _handle_watchlist(event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        await watchlist_matcher.finish(_group_only_hint())

    scope, group_id = _context(event)
    await service.startup()

    players = store.players(scope)
    muted = store.muted(group_id)
    rows = []
    for key in players:
        try:
            uid = int(key)
        except (TypeError, ValueError):
            continue
        record = players.get(key) or {}
        rows.append(
            {
                "uid": uid,
                "nick": str(record.get("nick") or ""),
                "pending": len(service.pending(scope, uid)),
                "muted": uid in muted,
            }
        )
    rows.sort(key=lambda row: (-row["pending"], row["uid"]))
    await watchlist_matcher.finish(
        render_watch_list(rows, len(service.active_activities()), config.hoyo_events_max_watch)
    )


# --------------------------------------------------------------------------- 提醒设置
push_matcher = on_command(
    "活动提醒",
    aliases={"活动推送", "活动订阅"},
    priority=10,
    block=True,
)


@push_matcher.handle()
async def _handle_push_settings(
    bot: Bot, event: MessageEvent, args: Message = CommandArg()
) -> None:
    tokens = _split_tokens(args.extract_plain_text())
    action = tokens[0] if tokens else "状态"

    if not isinstance(event, GroupMessageEvent):
        await push_matcher.finish(_group_only_hint())

    group_id = event.group_id
    record = store.group(group_id)
    enabled = bool(record.get("enabled"))
    hour, minute = group_push_time(record)
    push_time = f"{hour:02d}:{minute:02d}"

    if action in {"状态", "查看", "info", "status"}:
        total_active = len(service.active_activities())
        muted = int(event.get_user_id()) in store.muted(group_id)
        await push_matcher.finish(
            render_status(
                enabled=enabled,
                push_time=push_time,
                players=len(store.players(build_scope_key(group_id))),
                data_status=service.data_status(),
                total_active=total_active,
                muted=muted,
            )
        )

    if action in {"开", "开启", "on", "启用"}:
        if not _can_manage_push(event):
            await push_matcher.finish(admin_only)
        store.update_group(group_id, enabled=True)
        await store.commit()
        await push_matcher.finish(
            f"✅ 已开启每日活动推送，每天 {push_time} 播报并 @ 未完成的群友。\n"
            "发送「活动提醒 时间 22:00」可以改时间。"
        )

    if action in {"关", "关闭", "off", "停用"}:
        if not _can_manage_push(event):
            await push_matcher.finish(admin_only)
        store.update_group(group_id, enabled=False)
        await store.commit()
        await push_matcher.finish("已关闭每日活动推送。")

    if action in {"时间", "定时", "time", "设置时间"}:
        if not _can_manage_push(event):
            await push_matcher.finish(admin_only)
        value = tokens[1] if len(tokens) > 1 else ""
        parsed = parse_push_time(value)
        if not parsed:
            await push_matcher.finish("时间格式不对哦，正确写法：活动提醒 时间 21:30")
        hour, minute = parsed
        store.update_group(group_id, hour=hour, minute=minute, enabled=True)
        await store.commit()
        await push_matcher.finish(
            f"✅ 每日推送时间已设为 {hour:02d}:{minute:02d}（同时开启了推送）。"
        )

    if action in {"立即", "马上", "now", "推送"}:
        if not _can_manage_push(event):
            await push_matcher.finish(admin_only)
        result = await push_group_once(group_id, bot=bot)
        if result:
            await push_matcher.finish()
        await push_matcher.finish("这次推送失败了，可能是数据还没拉到或者机器人不在群里。")

    await push_matcher.finish(
        "用法：\n 活动提醒 开 / 关\n 活动提醒 时间 21:30\n 活动提醒 状态\n 活动提醒 立即"
    )


# --------------------------------------------------------------------------- 刷新
refresh_matcher = on_command(
    "活动刷新",
    aliases={"刷新活动"},
    priority=10,
    block=True,
)


@refresh_matcher.handle()
async def _handle_refresh(event: MessageEvent) -> None:
    if not _is_superuser(event):
        await refresh_matcher.finish(superuser_only)
    results = await service.ensure_data(force=True)
    summary = service.refresh_summary(results)
    await refresh_matcher.finish(f"🔄 已刷新活动数据\n{summary}")
