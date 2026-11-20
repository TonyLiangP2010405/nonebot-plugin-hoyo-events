from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .config import Config
from .datasource import ActivitySource, FetchResult
from .games import GAME_BY_KEY, GAME_KEYS, game_order, resolve_game
from .models import (
    Activity,
    Entry,
    View,
    fmt_remaining,
    normalize_name,
    now,
    parse_datetime,
)
from .settings import GroupSettings, SettingsResolver
from .store import DataStore

logger = logging.getLogger("nonebot_plugin_hoyo_events")

# 视图缓存时长：超过后编号（1、2、3…）作废，只能按名称标记
VIEW_TTL = 6 * 3600
VIEW_LIMIT = 800

ALL_TOKENS = {"全部", "所有", "全部活动", "all", "ALL", "a"}


def scope_of_private(user_id: int) -> str:
    return f"private_{user_id}"


@dataclass
class CompletionOutcome:
    matched: list = field(default_factory=list)
    newly: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def changed(self) -> list:
        return self.newly or self.removed


@dataclass
class AtTarget:
    activity: Activity
    users: list = field(default_factory=list)
    total: int = 0


class ActivityService:
    """业务逻辑：列表、完成状态、@ 目标、推送。"""

    def __init__(
        self,
        store: DataStore,
        source: ActivitySource,
        config: Config,
        settings: Optional[SettingsResolver] = None,
    ) -> None:
        self.store = store
        self.source = source
        self.config = config
        self.settings = settings or SettingsResolver(store, config)
        self._views: dict = {}
        self._started = False

    def group_settings(self, group_id: Optional[int]) -> GroupSettings:
        return self.settings.group(group_id)

    # ------------------------------------------------------------------ 启动
    async def startup(self) -> None:
        if self._started:
            return
        await self.store.load()
        self.settings.apply_to_source(self.source)
        self._started = True

    async def ensure_data(self, force: bool = False) -> dict:
        await self.startup()
        try:
            return await self.source.ensure_fresh(force=force)
        except Exception as exc:  # pragma: no cover - 兜底，绝不因为网络问题炸掉命令
            logger.warning("刷新活动数据时出现异常：%s", exc)
            return {}

    def has_data(self) -> bool:
        return any(self.store.snapshot(game) for game in GAME_KEYS)

    # ------------------------------------------------------------------ 查询
    def active_activities(self, game: Optional[str] = None, at: Optional[datetime] = None) -> list:
        at = at or now()
        games = [game] if game else list(GAME_KEYS)
        result = []
        for key in games:
            for activity in self.source.activities(key):
                if activity.is_active(at):
                    result.append(activity)
        result.sort(key=lambda act: (game_order(act.game), act.end, act.start))
        return result

    def upcoming_activities(
        self,
        game: Optional[str] = None,
        at: Optional[datetime] = None,
        within_days: int = 3,
    ) -> list:
        at = at or now()
        limit = at + timedelta(days=max(0, within_days))
        games = [game] if game else list(GAME_KEYS)
        result = []
        for key in games:
            for activity in self.source.activities(key):
                if at < activity.start <= limit:
                    result.append(activity)
        result.sort(key=lambda act: (act.start, game_order(act.game)))
        return result

    def build_view(
        self,
        game: Optional[str] = None,
        scope: str = "",
        user_id: int = 0,
        at: Optional[datetime] = None,
        only_pending: bool = False,
        group_id: Optional[int] = None,
        *,
        only_completed: bool = False,
    ) -> View:
        at = at or now()
        max_events = self.settings.group(group_id).max_events
        activities = self.active_activities(game, at)

        done = self.store.done_uids(scope, user_id) if scope else set()
        if only_completed:
            activities = [act for act in activities if act.uid in done]
        elif only_pending:
            activities = [act for act in activities if act.uid not in done]

        # 每个游戏最多展示 N 条
        limited: list = []
        per_game: dict = {}
        for activity in activities:
            count = per_game.get(activity.game, 0)
            if count >= max_events:
                continue
            per_game[activity.game] = count + 1
            limited.append(activity)

        view = View(at=at)
        for index, activity in enumerate(limited, start=1):
            view.entries.append(Entry(index=index, activity=activity, done=activity.uid in done))

        if not only_completed:
            upcoming = self.upcoming_activities(game, at, within_days=3)
            shown = {entry.activity.uid for entry in view.entries}
            view.upcoming = [act for act in upcoming if act.uid not in shown][:6]

        for key in GAME_KEYS:
            if not any(entry.activity.game == key for entry in view.entries):
                continue
            snapshot = self.store.snapshot(key)
            version = snapshot.get("version") or ""
            version_name = snapshot.get("version_name") or ""
            header = ""
            if version:
                header = f"v{version}"
                if version_name:
                    header += f" {version_name}"
                parsed_end = parse_datetime(snapshot.get("end"))
                if parsed_end and parsed_end > at:
                    header += f" · 版本{fmt_remaining(parsed_end, at)}"
            view.headers[key] = header
        return view

    # ------------------------------------------------------------------ 视图缓存
    def remember_view(self, key: str, view: View) -> None:
        if not key:
            return
        self._views[key] = view
        if len(self._views) > VIEW_LIMIT:
            ordered = sorted(self._views.items(), key=lambda item: item[1].at)
            for old_key, _ in ordered[: len(self._views) - VIEW_LIMIT]:
                self._views.pop(old_key, None)

    def get_view(self, *keys: str) -> Optional[View]:
        """取最新的一份有效视图。"""

        best: Optional[View] = None
        limit = now() - timedelta(seconds=VIEW_TTL)
        for key in keys:
            view = self._views.get(key)
            if not view or view.at < limit:
                continue
            if best is None or view.at > best.at:
                best = view
        return best

    def view_by_index(self, index: int, *keys: str) -> Optional[Activity]:
        view = self.get_view(*keys)
        if not view:
            return None
        entry = view.by_index(index)
        return entry.activity if entry else None

    # ------------------------------------------------------------------ 解析目标
    def _match_name(self, text: str, activities: list) -> list:
        normalized = normalize_name(text)
        if not normalized:
            return []
        exact = [act for act in activities if normalize_name(act.name) == normalized]
        if exact:
            return exact
        contains = [
            act
            for act in activities
            if normalized in normalize_name(act.name) or normalize_name(act.name) in normalized
        ]
        if contains:
            return contains
        pool = {normalize_name(act.name): act for act in activities}
        hits = difflib.get_close_matches(normalized, list(pool), n=1, cutoff=0.62)
        return [pool[hit] for hit in hits]

    def resolve_tokens(
        self,
        tokens: list,
        scope: str,
        user_id: int,
        at: Optional[datetime] = None,
        view_keys: tuple = (),
        *,
        list_command: str = "活动",
    ) -> CompletionOutcome:
        """把「已完成 1 2 幽境危战 原神 全部」这类输入解析成活动列表。"""

        at = at or now()
        outcome = CompletionOutcome()
        active = self.active_activities(None, at)
        picked: dict = {}

        for token in tokens:
            text = token.strip()
            if not text:
                continue

            if text.lower() in {item.lower() for item in ALL_TOKENS}:
                for activity in active:
                    picked[activity.uid] = activity
                continue

            game = self._as_game(text)
            if game:
                matched_game = [act for act in active if act.game == game.key]
                if not matched_game:
                    outcome.errors.append(f"{game.short}当前没有进行中的活动")
                for activity in matched_game:
                    picked[activity.uid] = activity
                continue

            if text.isdigit():
                index = int(text)
                activity = self.view_by_index(index, *view_keys)
                if not activity:
                    outcome.errors.append(
                        f"找不到第 {index} 项，请重新发送「{list_command}」查看编号"
                    )
                elif activity.is_over(at):
                    outcome.errors.append(f"{activity.name} 已经结束了")
                else:
                    picked[activity.uid] = activity
                continue

            matched = self._match_name(text, active)
            if not matched:
                outcome.errors.append(f"没找到活动：{text}")
                continue
            for activity in matched:
                picked[activity.uid] = activity

        outcome.matched = sorted(picked.values(), key=lambda act: (game_order(act.game), act.end))
        return outcome

    @staticmethod
    def _as_game(text: str):
        """判断一个 token 是不是「整个游戏」，避免把活动名误当游戏名。"""

        candidate = text.strip()
        if not candidate or len(candidate) > 10:
            return None
        for suffix in ("活动", "的活动", "活动列表"):
            if candidate.endswith(suffix) and len(candidate) > len(suffix):
                candidate = candidate[: -len(suffix)]
                break
        game = resolve_game(candidate)
        if not game:
            return None
        cleaned = candidate.strip().lower()
        if cleaned in game.aliases or cleaned in (game.name.lower(), game.short.lower()):
            return game
        return None

    # ------------------------------------------------------------------ 完成状态
    async def mark_completed(
        self,
        scope: str,
        user_id: int,
        tokens: list,
        nick: str = "",
        view_keys: tuple = (),
    ) -> CompletionOutcome:
        outcome = self.resolve_tokens(tokens, scope, user_id, view_keys=view_keys)
        if outcome.matched:
            uids = [act.uid for act in outcome.matched]
            outcome.newly = self.store.mark_done(scope, user_id, uids, nick=nick)
            await self.store.commit()
        return outcome

    async def unmark_completed(
        self,
        scope: str,
        user_id: int,
        tokens: list,
        view_keys: tuple = (),
    ) -> CompletionOutcome:
        outcome = self.resolve_tokens(
            tokens, scope, user_id, view_keys=view_keys, list_command="取消完成"
        )
        if outcome.matched:
            uids = [act.uid for act in outcome.matched]
            outcome.removed = self.store.unmark_done(scope, user_id, uids)
            if outcome.removed:
                await self.store.commit()
            else:
                outcome.matched.clear()
                outcome.errors.append("这些活动没有标记完成。发送「取消完成」查看可撤销的活动。")
        return outcome

    def pending(self, scope: str, user_id: int, at: Optional[datetime] = None) -> list:
        done = self.store.done_uids(scope, user_id)
        return [act for act in self.active_activities(None, at) if act.uid not in done]

    def done_count(self, scope: str, user_id: int, at: Optional[datetime] = None) -> tuple:
        active = self.active_activities(None, at)
        done = self.store.done_uids(scope, user_id)
        finished = [act for act in active if act.uid in done]
        return len(finished), len(active)

    # ------------------------------------------------------------------ @ 目标
    def at_targets(
        self,
        scope: str,
        activities: list,
        group_id: Optional[int] = None,
        at: Optional[datetime] = None,
    ) -> list:
        """按活动分组，找出该群里「这个活动还没完成」的人。"""

        at = at or now()
        players = self.store.players(scope)
        if not players:
            return []
        max_users = self.settings.group(group_id).max_at_users
        muted = self.store.muted(group_id) if group_id is not None else set()
        targets = []
        for activity in activities:
            missing = []
            for player_id in players:
                try:
                    uid = int(player_id)
                except (TypeError, ValueError):
                    continue
                if uid in muted:
                    continue
                if activity.uid in self.store.completions(scope, uid):
                    continue
                missing.append(uid)
            if not missing:
                continue
            total = len(missing)
            targets.append(
                AtTarget(
                    activity=activity,
                    users=missing[:max_users],
                    total=total,
                )
            )
        return targets

    def person_statuses(self, scope: str, view: View, group_id: Optional[int] = None) -> list:
        """按人统计：谁还差哪些编号的活动（用于「按人」的 @ 名单）。"""

        players = self.store.players(scope)
        muted = self.store.muted(group_id) if group_id is not None else set()
        rows = []
        for player_id in players:
            try:
                uid = int(player_id)
            except (TypeError, ValueError):
                continue
            if uid in muted:
                continue
            done = self.store.done_uids(scope, uid)
            done_idx = [entry.index for entry in view.entries if entry.activity.uid in done]
            pending_idx = [entry.index for entry in view.entries if entry.activity.uid not in done]
            if not pending_idx:
                continue
            rows.append({"uid": uid, "done": done_idx, "pending": pending_idx})
        rows.sort(key=lambda row: (-len(row["pending"]), row["uid"]))
        return rows

    def pending_by_user(self, scope: str, at: Optional[datetime] = None) -> dict:
        """``{用户id: [未完成活动]}``，推送时用来分组。"""

        at = at or now()
        active = self.active_activities(None, at)
        result: dict = {}
        for player_id in self.store.players(scope):
            try:
                uid = int(player_id)
            except (TypeError, ValueError):
                continue
            done = self.store.done_uids(scope, uid)
            missing = [act for act in active if act.uid not in done]
            if missing:
                result[uid] = missing
        return result

    # ------------------------------------------------------------------ 状态文案
    def data_status(self) -> str:
        return self.source.data_age_text()

    def refresh_summary(self, results: dict) -> str:
        if not results:
            return "未获取到数据"
        lines = []
        for game in GAME_KEYS:
            result = results.get(game)
            if not isinstance(result, FetchResult):
                continue
            lines.append(("✅ " if result.ok and not result.stale else "⚠️ ") + result.describe())
        return "\n".join(lines)


def build_view_key(group_id: Optional[int], user_id: int) -> str:
    if group_id is None:
        return f"p{user_id}"
    return f"g{group_id}:{user_id}"


def build_scope_key(group_id: Optional[int]) -> str:
    if group_id is None:
        return ""
    return f"g{group_id}"


__all__ = [
    "ActivityService",
    "AtTarget",
    "CompletionOutcome",
    "build_scope_key",
    "build_view_key",
    "scope_of_private",
    "GAME_BY_KEY",
]
