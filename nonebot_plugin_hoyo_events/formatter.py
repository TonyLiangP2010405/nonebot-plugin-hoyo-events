from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .games import GAME_BY_KEY
from .models import Activity, View, fmt_datetime, fmt_md

LINE = "━━━━━━━━━━"
MAX_LINES = 42
NAME_LIMIT = 26


def _clip(text: str, limit: int = NAME_LIMIT) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _header(view: View, game: str) -> str:
    meta = GAME_BY_KEY.get(game)
    prefix = f"{meta.emoji}【{meta.short}】" if meta else f"【{game}】"
    detail = view.headers.get(game) or ""
    return f"{prefix}{detail}".strip()


def render_view(
    view: View,
    remind_days: int = 3,
    show_legend: bool = True,
    title: str = "",
    footer: str = "",
    at: Optional[datetime] = None,
) -> str:
    """把活动列表渲染成文本（编号与 view.entries 一一对应）。"""

    at = at or view.at
    if not view.entries:
        return "当前没有进行中的活动。"

    lines = [title or f"🎮 米哈游活动 · {fmt_md(at)}", LINE]

    current = None
    shown = 0
    urgent_shown = False
    done_shown = False
    for entry in view.entries:
        if shown >= MAX_LINES:
            break
        activity = entry.activity
        if activity.game != current:
            if current is not None:
                lines.append("")
            lines.append(_header(view, activity.game))
            current = activity.game

        remaining = entry.remaining(at)
        left = entry.activity.days_left(at)
        flag = ""
        if 0 <= left <= remind_days and activity.end > at:
            flag = " ⏰"
            urgent_shown = True
        if entry.done:
            done_shown = True

        status = " ✓" if entry.done else ""
        lines.append(f" {entry.index}. {_clip(activity.name)} · {remaining}{flag}{status}")
        shown += 1

    hidden = len(view.entries) - shown
    if hidden > 0:
        lines.append(f" …还有 {hidden} 个活动未显示")

    if view.upcoming:
        lines.append("")
        lines.append("🕐 即将开始")
        for activity in view.upcoming:
            meta = GAME_BY_KEY.get(activity.game)
            prefix = meta.short if meta else activity.game
            lines.append(
                f" · [{prefix}] {_clip(activity.name)} · {fmt_datetime(activity.start)} 开始"
            )

    if show_legend:
        lines.append(LINE)
        parts = []
        if urgent_shown:
            parts.append(f"⏰ {remind_days} 天内结束")
        if done_shown:
            parts.append("✓ 你已完成")
        if not parts:
            parts.append(f"共 {len(view.entries)} 个进行中的活动")
        lines.append(" · ".join(parts))

    if footer:
        lines.append(footer)

    return "\n".join(lines)


@dataclass
class PushMessage:
    """推送消息：标题 + 正文 + 分段 @ 名单 + 结尾。"""

    head: str = ""
    targets: list = field(default_factory=list)
    tail: str = ""
    view: Optional[View] = None
    overflow: Optional[tuple] = None
    """活动太多时合并的一行：(剩余活动数, 待 @ 的用户)。"""

    @property
    def has_targets(self) -> bool:
        return bool(self.targets or self.overflow)


def render_push(
    view: View,
    targets: list,
    remind_days: int = 3,
    title: str = "",
    no_player_hint: bool = False,
    max_at_lines: int = 12,
) -> PushMessage:
    head = render_view(
        view,
        remind_days=remind_days,
        show_legend=True,
        title=title or f"📢 今日活动提醒 · {fmt_md(view.at)}",
        footer="",
    )

    overflow = None
    if max_at_lines > 0 and len(targets) > max_at_lines:
        rest = targets[max_at_lines:]
        merged: list = []
        for target in rest:
            for uid in target.users:
                if uid not in merged:
                    merged.append(uid)
        overflow = (len(rest), merged)
        targets = targets[:max_at_lines]

    # 「还没完成的群友」标题必须放在正文尾部（@ 名单之前），
    # 因为正文和 @ 名单是分两段拼的
    if targets:
        head = f"{head}\n{LINE}\n还没完成的群友："

    tail_lines = []
    if not targets and no_player_hint:
        tail_lines.append(
            f"{LINE}\n还没有人登记活动进度，发送「活动」即可加入每日提醒（只 @ 未完成的人）。"
        )

    if targets:
        tail_lines.append("发送「已完成 编号」即可不再被 @，例如：已完成 1 3")
    return PushMessage(
        head=head, targets=targets, tail="\n".join(tail_lines), view=view, overflow=overflow
    )


def render_target_line(target) -> str:
    """单条 @ 名单前面的活动标题。"""

    activity: Activity = target.activity
    left = activity.days_left()
    suffix = f"（{target.total} 人）" if target.total > len(target.users) else ""
    return f"· {_clip(activity.name, 18)} · 剩 {left} 天{suffix}："


def render_completion(outcome, scope_pending: int, scope_total: int) -> str:
    lines = []
    if outcome.newly:
        lines.append(f"✅ 已记录完成 {len(outcome.newly)} 个活动")
        for activity in outcome.matched:
            if activity.uid in outcome.newly:
                left = activity.days_left()
                lines.append(f" · {_clip(activity.name, 20)}（还剩 {left} 天）")
    elif outcome.removed:
        lines.append(f"↩️ 已取消 {len(outcome.removed)} 个活动的完成标记")
        for activity in outcome.matched:
            if activity.uid in outcome.removed:
                lines.append(f" · {_clip(activity.name, 20)}")
    elif outcome.matched:
        names = "、".join(_clip(act.name, 12) for act in outcome.matched[:3])
        lines.append(f"这几个活动之前就标记过完成了：{names}")
    if outcome.errors:
        lines.append("⚠️ " + "；".join(outcome.errors))

    if scope_total:
        lines.append("")
        lines.append(f"你的进度：{scope_total - scope_pending}/{scope_total} 已完成")
        if scope_pending:
            lines.append("发送「未完成」查看还差哪些。")
        else:
            lines.append("全部活动都完成啦，后续不会再 @ 你 🎉")
    return "\n".join(lines).strip()


def render_unmark(outcome, pending: int) -> str:
    return render_completion(outcome, pending, pending)


def render_watch_list(rows: list, total_active: int, limit: int = 0) -> str:
    """本群提醒名单。rows: [{uid, nick, pending, muted}]"""

    if not rows:
        return (
            "📋 本群还没有人在提醒名单里。\n"
            "发送「活动关注」把自己加进来，或者「活动关注 @某人」把队友一起加上。"
        )

    lines = [f"📋 本群提醒名单（{len(rows)} 人 · 进行中活动 {total_active} 个）", LINE]
    for index, row in enumerate(rows[:30], start=1):
        name = _display(row)
        if row.get("muted"):
            tail = "已免打扰"
        elif row.get("pending") == 0:
            tail = "全部完成 ✅"
        else:
            tail = f"未完成 {row['pending']}"
        lines.append(f" {index}. {_clip(name, 14)} · {tail}")
    if len(rows) > 30:
        lines.append(f" …还有 {len(rows) - 30} 人")
    lines.append(LINE)
    if limit:
        lines.append(f"名单上限 {limit} 人 · 发送「活动关注 @某人」加人，「活动取关」退出")
    else:
        lines.append("发送「活动关注 @某人」加人，「活动取关」退出")
    return "\n".join(lines)


def _display(row: dict) -> str:
    return str(row.get("nick") or row.get("uid") or "")


def render_settings(rows: list, global_rows: list, push_time: str = "") -> str:
    """rows: [(label, value_text, is_custom, hint)]"""

    lines = ["⚙️ 本群活动设置", LINE]
    for label, value, custom, _hint in rows:
        mark = "（已改）" if custom else ""
        lines.append(f" · {label}：{value}{mark}")

    if global_rows:
        lines.append("")
        lines.append("🌐 全局设置（超管）")
        for label, value, custom, _hint in global_rows:
            mark = "（已改）" if custom else ""
            lines.append(f" · {label}：{value}{mark}")

    lines.append(LINE)
    if push_time:
        lines.append(f"当前推送时间：{push_time}")
    lines.append("改法：活动设置 <项> <值>，例如「活动设置 时间 21:30」")
    lines.append("全部可改项：活动设置 帮助")
    return "\n".join(lines)


def render_settings_help(rows: list) -> str:
    """rows: [(label, kind_hint, hint, aliases)]"""

    kind_hint = {
        "bool": "开/关",
        "int": "数字",
        "time": "21:30",
        "choice": "群管/所有人",
        "url": "https://…",
    }
    lines = ["⚙️ 可以这样改设置", LINE]
    for label, kind, hint, aliases in rows:
        alias_text = " / ".join(aliases[:2]) if aliases else label
        value_hint = kind_hint.get(kind, "值")
        suffix = f"，{hint}" if hint else ""
        lines.append(f" · {label}（{alias_text}，填 {value_hint}{suffix}）")
    lines.append(LINE)
    lines.append("例子：")
    lines.append(" 活动设置 推送 开")
    lines.append(" 活动设置 时间 21:30")
    lines.append(" 活动设置 加急天数 5")
    lines.append(" 活动设置 权限 所有人")
    return "\n".join(lines)


def render_help(push_time: str, remind_days: int) -> str:
    return "\n".join(
        [
            "🎮 米哈游活动助手",
            LINE,
            "【查活动】",
            " · 活动 —— 三个游戏的活动总览（含剩余天数）",
            " · 原神活动 / 星铁活动 / 绝区零活动 —— 只看一个游戏",
            " · 活动 提醒 —— 顺便 @ 还没完成的群友",
            "",
            "【标记完成】",
            " · 已完成 —— 一键完成当前所有活动",
            " · 已完成 1 3 —— 按列表编号标记",
            " · 已完成 幽境危战 —— 按活动名标记（支持模糊匹配）",
            " · 已完成 原神 —— 完成某个游戏的全部活动",
            " · 取消完成 1 —— 撤销标记",
            " · 未完成 —— 查看自己还差哪些",
            "",
            "【提醒名单（谁都能用）】",
            " · 活动关注 —— 把自己加进提醒名单",
            " · 活动关注 @小明 @小红 —— 把别人也加进来（不需要群管）",
            " · 活动取关 [@小明] —— 退出名单 / 移除自己加的人",
            " · 活动名单 —— 看看本群谁在名单里、还差几个活动",
            "",
            "【提醒设置（命令改，不用改配置文件）】",
            " · 活动设置 —— 查看本群所有设置",
            " · 活动设置 推送 开|关 —— 开关每日推送",
            f" · 活动设置 时间 21:30 —— 推送时间（默认 {push_time}）",
            " · 活动设置 加急天数 3 —— 几天内标 ⏰",
            " · 活动设置 权限 群管|所有人 —— 谁能改设置、开推送",
            " · 活动设置 帮助 —— 所有可改项",
            " · 活动提醒 立即 —— 立刻推送一次",
            " · 活动免打扰 —— 自己不再被 @",
            "",
            "【其它】",
            " · 活动刷新 —— 管理员手动拉取最新活动",
            f" · 结束前 {remind_days} 天的活动会带 ⏰ 提醒",
            "标记完成是按「群 + 人 + 活动」分别记录的，",
            "只完成活动 1 不会影响其他活动的 @ 提醒。",
        ]
    )


def render_status(
    enabled: bool,
    push_time: str,
    players: int,
    data_status: str,
    total_active: int,
    muted: bool,
) -> str:
    lines = [
        "⚙️ 活动提醒设置",
        LINE,
        f" · 每日推送：{'已开启' if enabled else '已关闭'}",
        f" · 推送时间：{push_time}",
        f" · 登记人数：{players} 人",
        f" · 我的提醒：{'已免打扰' if muted else '正常'}",
        f" · 进行中活动：{total_active} 个",
        f" · 数据状态：{data_status}",
    ]
    if not enabled:
        lines.append("")
        lines.append("发送「活动提醒 开」即可开启每日推送。")
    return "\n".join(lines)


__all__ = [
    "PushMessage",
    "render_settings",
    "render_settings_help",
    "render_completion",
    "render_help",
    "render_push",
    "render_status",
    "render_target_line",
    "render_unmark",
    "render_view",
]
