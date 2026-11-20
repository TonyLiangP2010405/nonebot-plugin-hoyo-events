from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from nonebot import get_plugin_config
from nonebot_plugin_localstore import get_plugin_data_dir

from .config import Config
from .datasource import ActivitySource
from .service import ActivityService
from .settings import SettingsResolver
from .store import DataStore

logger = logging.getLogger("nonebot_plugin_hoyo_events")

config: Config = get_plugin_config(Config)

OVERRIDE_TEMPLATE = {
    "_说明": [
        "手动修正活动数据（可选）。修改后无需重启，下一条命令会自动生效。",
        "add：追加数据源没有的活动；remove：按活动名删除；patch：按活动名改时间/描述。",
        "时间格式：2026-12-01T10:00:00 或 2026-12-01 10:00:00",
    ],
    "ys": {"add": [], "remove": [], "patch": []},
    "sr": {"add": [], "remove": [], "patch": []},
    "zzz": {"add": [], "remove": [], "patch": []},
}


def data_dir() -> Path:
    """数据目录，支持用环境变量覆盖（测试 / 迁移用）。"""

    override = os.getenv("HOYO_EVENTS_DATA_DIR")
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return get_plugin_data_dir()


def ensure_override_file(path: Path) -> None:
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(OVERRIDE_TEMPLATE, ensure_ascii=False, indent=2), "utf-8")
    except OSError as exc:  # pragma: no cover - 只读环境
        logger.debug("写入活动覆盖模板失败：%s", exc)


DATA_DIR = data_dir()
STORE_PATH = DATA_DIR / "store.json"
OVERRIDE_PATH = DATA_DIR / "override.json"
ensure_override_file(OVERRIDE_PATH)

store = DataStore(STORE_PATH)
source = ActivitySource(
    store=store,
    api_base=config.hoyo_events_api_base,
    timeout=config.hoyo_events_http_timeout,
    ttl=config.hoyo_events_cache_ttl,
    override_path=OVERRIDE_PATH,
)
settings = SettingsResolver(store=store, config=config)
service = ActivityService(store=store, source=source, config=config, settings=settings)


def parse_push_time(text: str) -> Optional[tuple]:
    """解析 ``HH:MM``，返回 (hour, minute)。"""

    text = (text or "").strip().replace("：", ":")
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 2:
        parts = [text, "0"]
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def default_push_time() -> tuple:
    return parse_push_time(config.hoyo_events_push_time) or (21, 0)


__all__ = [
    "config",
    "settings",
    "DATA_DIR",
    "OVERRIDE_PATH",
    "STORE_PATH",
    "default_push_time",
    "parse_push_time",
    "service",
    "source",
    "store",
]
