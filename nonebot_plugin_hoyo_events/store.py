from __future__ import annotations

import asyncio
import json
import logging
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .models import iso, now

logger = logging.getLogger("nonebot_plugin_hoyo_events")

SCHEMA_VERSION = 1

# 完成状态最多保留多少天的历史，避免文件无限膨胀
_COMPLETION_RETENTION_DAYS = 400
_REGISTRY_LIMIT = 800


def default_data() -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "snapshots": {},
        "registry": {},
        "completions": {},
        "players": {},
        "groups": {},
        "meta": {},
    }


class DataStore:
    """极简 JSON 存储：原子写入 + 单文件锁。

    数据量很小（群数 × 人数 × 活动数为百级），没必要上数据库。
    所有写操作都是同步的内存修改，改完调用 :meth:`commit` 落盘即可。
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict = default_data()
        self._io_lock = asyncio.Lock()
        self._loaded = False

    # ------------------------------------------------------------------ 生命周期
    @property
    def data(self) -> dict:
        return self._data

    async def load(self) -> None:
        if self._loaded:
            return
        async with self._io_lock:
            if self._loaded:
                return
            await asyncio.to_thread(self._load_sync)
            self._loaded = True

    def _load_sync(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("活动数据文件损坏，将重新创建：%s", exc)
            try:
                backup = self.path.with_suffix(f".broken-{int(now().timestamp())}.json")
                self.path.replace(backup)
                logger.warning("已备份损坏的数据文件到 %s", backup)
            except OSError:
                pass
            return
        if not isinstance(raw, dict):
            logger.warning("活动数据文件格式异常，已忽略")
            return
        merged = default_data()
        for key, value in merged.items():
            if key in raw and isinstance(raw[key], type(value)):
                merged[key] = raw[key]
        # 兼容旧版本：缺失字段时补默认值
        for section in ("snapshots", "registry", "completions", "players", "groups", "meta"):
            if not isinstance(merged.get(section), dict):
                merged[section] = {}
        self._data = merged

    async def commit(self) -> None:
        async with self._io_lock:
            payload = deepcopy(self._data)
            await asyncio.to_thread(self._write_sync, payload)

    def _write_sync(self, payload: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:  # pragma: no cover - 磁盘异常
            logger.error("保存活动数据失败：%s", exc)

    # ------------------------------------------------------------------ 数据快照
    def snapshot(self, game: str) -> dict:
        value = self._data["snapshots"].get(game)
        return value if isinstance(value, dict) else {}

    def snapshots(self) -> dict:
        return self._data["snapshots"]

    def set_snapshot(self, game: str, payload: dict) -> None:
        self._data["snapshots"][game] = payload

    def snapshot_fetched_at(self, game: str) -> Optional[datetime]:
        from .models import parse_datetime

        return parse_datetime(self.snapshot(game).get("fetched_at"))

    # ------------------------------------------------------------------ 活动身份
    def registry_entry(self, base_id: str) -> Optional[dict]:
        entry = self._data["registry"].get(base_id)
        return entry if isinstance(entry, dict) else None

    def set_registry_entry(self, base_id: str, entry: dict) -> None:
        self._data["registry"][base_id] = entry

    def prune_registry(self, at: Optional[datetime] = None) -> None:
        from .models import parse_datetime

        at = at or now()
        registry = self._data["registry"]
        if len(registry) <= _REGISTRY_LIMIT:
            return
        alive = []
        for base_id, entry in registry.items():
            last_seen = parse_datetime(entry.get("last_seen"))
            if last_seen and (at - last_seen).days > _COMPLETION_RETENTION_DAYS:
                continue
            alive.append((last_seen or at, base_id))
        alive.sort()
        for _, base_id in alive[: len(alive) - _REGISTRY_LIMIT]:
            registry.pop(base_id, None)

    # ------------------------------------------------------------------ 玩家
    def touch_player(self, scope: str, user_id: int, nick: str = "") -> None:
        players = self._data["players"].setdefault(scope, {})
        record = players.get(str(user_id))
        if not isinstance(record, dict):
            record = {"nick": "", "first_seen": iso(now())}
            players[str(user_id)] = record
        if nick:
            record["nick"] = nick
        record["last_seen"] = iso(now())

    def players(self, scope: str) -> dict:
        value = self._data["players"].get(scope)
        return value if isinstance(value, dict) else {}

    def forget_player(self, scope: str, user_id: int) -> None:
        self._data["players"].get(scope, {}).pop(str(user_id), None)

    # ------------------------------------------------------------------ 完成记录
    def completions(self, scope: str, user_id: int) -> dict:
        """返回 ``{活动uid: 完成时间}``。"""

        per_scope = self._data["completions"].get(scope)
        if not isinstance(per_scope, dict):
            return {}
        per_user = per_scope.get(str(user_id))
        return per_user if isinstance(per_user, dict) else {}

    def done_uids(self, scope: str, user_id: int) -> set:
        return set(self.completions(scope, user_id))

    def mark_done(self, scope: str, user_id: int, uids: list, nick: str = "") -> list:
        """标记完成，返回**新**记录的 uid（已完成的不会重复计数）。"""

        if not uids:
            return []
        per_scope = self._data["completions"].setdefault(scope, {})
        per_user = per_scope.get(str(user_id))
        if not isinstance(per_user, dict):
            per_user = {}
            per_scope[str(user_id)] = per_user
        stamp = iso(now())
        newly = []
        for uid in uids:
            if uid in per_user:
                continue
            per_user[uid] = stamp
            newly.append(uid)
        if nick:
            self.touch_player(scope, user_id, nick)
        return newly

    def unmark_done(self, scope: str, user_id: int, uids: list) -> list:
        per_scope = self._data["completions"].get(scope) or {}
        per_user = per_scope.get(str(user_id)) if isinstance(per_scope, dict) else None
        if not isinstance(per_user, dict):
            return []
        removed = []
        for uid in uids:
            if per_user.pop(uid, None) is not None:
                removed.append(uid)
        return removed

    def set_done(self, scope: str, user_id: int, uid: str, done: bool) -> bool:
        """兼容接口：返回状态是否发生变化。"""

        changed = (
            self.mark_done(scope, user_id, [uid])
            if done
            else self.unmark_done(scope, user_id, [uid])
        )
        return bool(changed)

    # ------------------------------------------------------------------ 群设置
    def group(self, group_id: int) -> dict:
        record = self._data["groups"].get(str(group_id))
        return record if isinstance(record, dict) else {}

    def update_group(self, group_id: int, **fields: Any) -> dict:
        groups = self._data["groups"]
        record = groups.get(str(group_id))
        if not isinstance(record, dict):
            record = {"enabled": False, "hour": 21, "minute": 0, "last_push": "", "muted": []}
            groups[str(group_id)] = record
        for key, value in fields.items():
            record[key] = value
        return record

    def all_groups(self) -> dict:
        return self._data["groups"]

    def muted(self, group_id: int) -> set:
        value = self.group(group_id).get("muted") or []
        return {int(item) for item in value if str(item).lstrip("-").isdigit()}

    def mute(self, group_id: int, user_id: int) -> None:
        record = self.update_group(group_id)
        muted = [int(item) for item in (record.get("muted") or [])]
        if user_id not in muted:
            muted.append(user_id)
        record["muted"] = muted

    def unmute(self, group_id: int, user_id: int) -> None:
        record = self.update_group(group_id)
        record["muted"] = [
            int(item) for item in (record.get("muted") or []) if int(item) != user_id
        ]

    # ------------------------------------------------------------------ meta
    def meta(self, key: str, value: Any = None) -> Any:
        if value is None:
            return self._data["meta"].get(key)
        self._data["meta"][key] = value
        return value

    def cleanup(self, at: Optional[datetime] = None) -> None:
        """清理过期完成记录。"""

        at = at or now()
        self.prune_registry(at)
        keep = set()
        for entry in self._data["registry"].values():
            if isinstance(entry, dict):
                keep.add(entry.get("start", ""))
        # 以 uid 的过期情况判断：靠 registry 里没有的 uid 直接丢弃
        valid_uids = set()
        for base_id, entry in self._data["registry"].items():
            generation = int(entry.get("generation") or 1)
            valid_uids.add(f"{base_id}#{generation}")
        for scope, per_scope in list(self._data["completions"].items()):
            if not isinstance(per_scope, dict):
                self._data["completions"].pop(scope, None)
                continue
            for uid_key, records in list(per_scope.items()):
                if not isinstance(records, dict):
                    per_scope.pop(uid_key, None)
                    continue
                for uid in list(records):
                    if uid not in valid_uids:
                        records.pop(uid, None)
                if not records:
                    per_scope.pop(uid_key, None)


__all__ = ["DataStore", "default_data", "SCHEMA_VERSION"]
