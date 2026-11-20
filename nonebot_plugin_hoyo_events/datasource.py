from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx

from .games import GAME_BY_KEY, GAME_KEYS
from .models import (
    Activity,
    event_base_id,
    iso,
    normalize_name,
    now,
    parse_datetime,
)
from .store import DataStore

logger = logging.getLogger("nonebot_plugin_hoyo_events")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 nonebot-plugin-hoyo-events/0.1.0"
)

# 活动数据里最多保存多少条原始记录
MAX_RAW_ACTIVITIES = 200


class SourceError(RuntimeError):
    """数据源不可用。"""


@dataclass
class FetchResult:
    game: str
    ok: bool = False
    count: int = 0
    error: str = ""
    stale: bool = False
    """是否沿用了本地缓存。"""

    added: list = field(default_factory=list)
    """本次刷新新出现的活动名。"""

    def describe(self) -> str:
        name = GAME_BY_KEY[self.game].short if self.game in GAME_BY_KEY else self.game
        if self.ok:
            return f"{name} {self.count} 个活动"
        if self.stale:
            return f"{name} 刷新失败（沿用缓存）：{self.error}"
        return f"{name} 刷新失败：{self.error}"


async def _http_get_json(url: str, timeout: float) -> Any:
    """单独抽出，方便测试时 monkeypatch。"""

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


def _raw_start(record: dict) -> Optional[datetime]:
    for key in ("startTime", "start_time", "start", "begin", "beginTime"):
        parsed = parse_datetime(record.get(key))
        if parsed:
            return parsed
    return None


def _raw_end(record: dict) -> Optional[datetime]:
    for key in ("endTime", "end_time", "end", "finish", "finishTime"):
        parsed = parse_datetime(record.get(key))
        if parsed:
            return parsed
    return None


def parse_payload(game: str, payload: Any, at: Optional[datetime] = None) -> tuple:
    """把数据源 JSON 解析成 (版本信息, 活动列表, 丢弃条数)。"""

    at = at or now()
    meta: dict = {"version": "", "version_name": "", "start": "", "end": "", "cover": ""}
    activities: list = []
    dropped = 0

    if not isinstance(payload, dict):
        raise SourceError("数据格式异常")

    meta["version"] = str(payload.get("version") or "")
    meta["version_name"] = str(payload.get("versionName") or payload.get("version_name") or "")
    start = parse_datetime(payload.get("startTime") or payload.get("start"))
    end = parse_datetime(payload.get("endTime") or payload.get("end"))
    meta["start"] = iso(start) if start else ""
    meta["end"] = iso(end) if end else ""
    meta["cover"] = str(payload.get("cover") or "")

    raw_activities = payload.get("activities")
    if not isinstance(raw_activities, list):
        raise SourceError("数据里没有 activities 字段")

    seen: dict = {}
    for record in raw_activities:
        if not isinstance(record, dict):
            dropped += 1
            continue
        name = str(record.get("name") or "").strip()
        start_at = _raw_start(record)
        end_at = _raw_end(record)
        if not name or not start_at or not end_at or end_at < start_at:
            dropped += 1
            continue
        key = normalize_name(name)
        # 同名活动出现多次时，保留结束时间更晚的那条
        if key in seen and end_at <= seen[key].end:
            continue
        seen[key] = Activity(
            uid="",  # 身份在 refresh 时按代次解析
            game=game,
            name=name,
            start=start_at,
            end=end_at,
            description=str(record.get("description") or ""),
            kind=str(record.get("kind") or ""),
            cover=str(record.get("cover") or ""),
        )

    activities = sorted(seen.values(), key=lambda item: (item.end, item.start))
    if len(activities) > MAX_RAW_ACTIVITIES:
        dropped += len(activities) - MAX_RAW_ACTIVITIES
        activities = activities[:MAX_RAW_ACTIVITIES]

    pools = []
    raw_pools = payload.get("pools")
    if isinstance(raw_pools, list):
        for record in raw_pools:
            if not isinstance(record, dict):
                continue
            name = str(record.get("name") or "").strip()
            start_at = _raw_start(record)
            end_at = _raw_end(record)
            if not name or not start_at or not end_at:
                continue
            pools.append(
                {
                    "name": name,
                    "type": str(record.get("type") or ""),
                    "start": iso(start_at),
                    "end": iso(end_at),
                    "up": [str(item) for item in (record.get("upCharacters") or [])],
                }
            )
    meta["pools"] = pools

    return meta, activities, dropped


class ActivitySource:
    """活动数据源：负责拉取、身份解析、缓存与手动覆盖。"""

    def __init__(
        self,
        store: DataStore,
        api_base: str,
        timeout: float = 15.0,
        ttl: int = 1800,
        override_path: Optional[Path] = None,
    ) -> None:
        self.store = store
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.ttl = ttl
        self.override_path = Path(override_path) if override_path else None
        self._override_cache: tuple = (None, {})
        self._refresh_lock = asyncio.Lock()

    # ------------------------------------------------------------------ 覆盖文件
    def override_data(self) -> dict:
        """读取 manual override 文件（按 mtime 缓存）。"""

        if not self.override_path or not self.override_path.exists():
            return {}
        try:
            mtime = self.override_path.stat().st_mtime
        except OSError:
            return {}
        cached_mtime, cached = self._override_cache
        if cached_mtime == mtime:
            return cached
        try:
            raw = json.loads(self.override_path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("活动覆盖文件解析失败，已忽略：%s", exc)
            return {}
        data = raw if isinstance(raw, dict) else {}
        self._override_cache = (mtime, data)
        return data

    def _apply_override(self, game: str, activities: list) -> list:
        override = self.override_data()
        rules = override.get(game)
        if not isinstance(rules, dict):
            return activities

        result = list(activities)
        remove_names = {
            normalize_name(str(item)) for item in (rules.get("remove") or []) if str(item).strip()
        }
        if remove_names:
            result = [item for item in result if normalize_name(item.name) not in remove_names]

        patch_map = {}
        for item in rules.get("patch") or []:
            if not isinstance(item, dict):
                continue
            name = normalize_name(str(item.get("name") or ""))
            if name:
                patch_map[name] = item
        for activity in result:
            patch = patch_map.get(normalize_name(activity.name))
            if not patch:
                continue
            start = parse_datetime(patch.get("startTime") or patch.get("start"))
            end = parse_datetime(patch.get("endTime") or patch.get("end"))
            if start:
                activity.start = start
            if end:
                activity.end = end
            if patch.get("description"):
                activity.description = str(patch["description"])
            if patch.get("kind"):
                activity.kind = str(patch["kind"])

        for item in rules.get("add") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            start = parse_datetime(item.get("startTime") or item.get("start"))
            end = parse_datetime(item.get("endTime") or item.get("end"))
            if not name or not start or not end:
                continue
            result = [act for act in result if normalize_name(act.name) != normalize_name(name)]
            result.append(
                Activity(
                    uid="",
                    game=game,
                    name=name,
                    start=start,
                    end=end,
                    description=str(item.get("description") or ""),
                    kind=str(item.get("kind") or ""),
                )
            )

        return sorted(result, key=lambda act: (act.end, act.start))

    # ------------------------------------------------------------------ 身份解析
    def resolve_identity(self, activity: Activity, at: Optional[datetime] = None) -> Activity:
        """为活动分配 (可能是新的) 稳定 ID。

        规则：
        - 同名活动默认沿用同一个基础 ID；
        - 只有当「旧记录已经结束」且「这次活动的开始时间明显更晚」时才认为是复刻/新一期，
          代次 +1（这样复刻活动的完成记录不会串到上一期）；
        - 活动延期（只改结束时间）不会改变 ID，所以已完成状态会保留。
        """

        at = at or now()
        base_id = event_base_id(activity.game, activity.name)
        generation = 1
        entry = self.store.registry_entry(base_id)
        if entry:
            try:
                generation = max(1, int(entry.get("generation") or 1))
            except (TypeError, ValueError):
                generation = 1
            old_start = parse_datetime(entry.get("start"))
            old_end = parse_datetime(entry.get("end"))
            if (
                old_start
                and old_end
                and old_end < at
                and (activity.start - old_start) > timedelta(hours=48)
            ):
                generation += 1

        activity.generation = generation
        activity.uid = f"{base_id}#{generation}"
        self.store.set_registry_entry(
            base_id,
            {
                "game": activity.game,
                "name": activity.name,
                "start": iso(activity.start),
                "end": iso(activity.end),
                "generation": generation,
                "last_seen": iso(at),
            },
        )
        return activity

    # ------------------------------------------------------------------ 网络
    async def _fetch(self, game: str) -> tuple:
        url = f"{self.api_base}/{game}.json"
        last_error = ""
        for attempt in range(2):
            try:
                payload = await _http_get_json(url, self.timeout)
                return parse_payload(game, payload)
            except (httpx.HTTPError, ValueError, SourceError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.debug("拉取 %s 活动失败（第 %s 次）：%s", game, attempt + 1, last_error)
                if attempt == 0:
                    await asyncio.sleep(1.0)
        raise SourceError(last_error or "未知错误")

    async def refresh(
        self,
        games: Optional[list] = None,
        force: bool = False,
        respect_ttl: bool = False,
    ) -> dict:
        """拉取活动数据。

        :param games: 只刷新指定游戏，默认全部。
        :param force: 跳过 TTL 检查。
        :param respect_ttl: 为 True 时，缓存未过期就跳过网络请求（查询场景用）。
        """

        results: dict = {}
        for game in games or list(GAME_KEYS):
            if game not in GAME_BY_KEY:
                continue
            snapshot = self.store.snapshot(game)
            if not force and respect_ttl and snapshot:
                fetched = self.store.snapshot_fetched_at(game)
                if fetched and (now() - fetched).total_seconds() < self.ttl:
                    results[game] = FetchResult(
                        game=game,
                        ok=True,
                        count=len(snapshot.get("activities") or []),
                    )
                    continue

            try:
                meta, parsed, dropped = await self._fetch(game)
            except SourceError as exc:
                has_cache = bool(snapshot.get("activities"))
                if snapshot:
                    snapshot["last_error"] = str(exc)
                results[game] = FetchResult(game=game, ok=False, error=str(exc), stale=has_cache)
                logger.warning("刷新 %s 活动失败：%s", game, exc)
                continue

            merged = self._apply_override(game, parsed)
            known = {
                item.get("uid")
                for item in (snapshot.get("activities") or [])
                if isinstance(item, dict)
            }
            activities = [self.resolve_identity(act) for act in merged]
            added = [act.name for act in activities if act.uid not in known]

            payload = {
                "game": game,
                **meta,
                "fetched_at": iso(now()),
                "source": "api",
                "activities": [act.to_dict() for act in activities],
                "dropped": dropped,
                "last_error": "",
            }
            self.store.set_snapshot(game, payload)
            results[game] = FetchResult(game=game, ok=True, count=len(activities), added=added)

        self.store.prune_registry()
        if any(item.ok for item in results.values()):
            self.store.meta("last_refresh", iso(now()))
        await self.store.commit()
        return results

    async def ensure_fresh(self, force: bool = False) -> dict:
        """查询前调用：只刷新过期或缺失的数据。"""

        return await self.refresh(force=force, respect_ttl=True)

    # ------------------------------------------------------------------ 读取
    def activities(self, game: str, at: Optional[datetime] = None) -> list:
        snapshot = self.store.snapshot(game)
        result = []
        for item in snapshot.get("activities") or []:
            activity = Activity.from_dict(item)
            if activity:
                result.append(activity)
        return result

    def all_activities(self) -> list:
        result = []
        for game in GAME_KEYS:
            result.extend(self.activities(game))
        return result

    def snapshot_age(self, game: str) -> Optional[float]:
        """缓存年龄（秒），没有数据时返回 None。"""

        fetched = self.store.snapshot_fetched_at(game)
        if not fetched:
            return None
        return (now() - fetched).total_seconds()

    def is_stale(self, game: str, threshold: int = 21600) -> bool:
        age = self.snapshot_age(game)
        return age is None or age > threshold

    def data_age_text(self) -> str:
        """「数据更新于 xx」文案。"""

        ages = [(game, self.snapshot_age(game)) for game in GAME_KEYS if self.store.snapshot(game)]
        if not ages:
            return "暂无数据"
        newest = max((age for _, age in ages if age is not None), default=None)
        if newest is None:
            return "暂无数据"
        if newest < 120:
            return "刚刚更新"
        if newest < 3600:
            return f"{int(newest // 60)} 分钟前更新"
        if newest < 86400:
            return f"{int(newest // 3600)} 小时前更新"
        return f"{int(newest // 86400)} 天前更新"


def describe_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "未更新"
    if seconds < 120:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"
