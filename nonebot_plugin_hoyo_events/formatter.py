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

    tail_lines = []
    if targets:
        tail_lines.append(f"{LINE}\n还没完成的群友：")
    elif no_player_hint:
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
            "【提醒设置（群管）】",
            " · 活动提醒 开 / 关 —— 开关每日推送",
            f" · 活动提醒 时间 21:30 —— 设置推送时间（默认 {push_time}）",
            " · 活动提醒 状态 —— 查看当前设置",
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
    "render_completion",
    "render_help",
    "render_push",
    "render_status",
    "render_target_line",
    "render_unmark",
    "render_view",
]
