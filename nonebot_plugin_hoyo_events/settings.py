"""运行期设置：全部通过命令修改，持久化在 store.json 里。

优先级：群设置（命令改的） > 全局设置（超管命令改的） > .env / 代码默认值。
.env 从「必须配置」变成「只是出厂默认值」，改不改都能用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .config import Config
from .store import DataStore


@dataclass(frozen=True)
class Option:
    key: str
    label: str
    kind: str  # bool / int / time / choice / url
    aliases: tuple = ()
    minimum: int = 0
    maximum: int = 0
    choices: tuple = ()
    hint: str = ""
    scope: str = "group"


GROUP_OPTIONS: tuple = (
    Option("push_enabled", "每日推送", "bool", ("推送", "push")),
    Option("push_time", "推送时间", "time", ("时间", "什么时候"), hint="21:30"),
    Option(
        "remind_days",
        "加急天数",
        "int",
        ("提醒天数", "加急", "days"),
        0,
        30,
        hint="剩余几天内加 ⏰",
    ),
    Option(
        "max_events",
        "每游戏条数",
        "int",
        ("条数", "显示条数", "max"),
        1,
        60,
        hint="每个游戏最多列几条",
    ),
    Option(
        "max_at_users",
        "单条@上限",
        "int",
        ("at上限", "艾特上限"),
        1,
        50,
        hint="一条活动最多 @ 几人",
    ),
    Option(
        "max_at_lines",
        "推送@行数",
        "int",
        ("at行数", "行数"),
        1,
        50,
        hint="推送里最多几个活动单独列 @",
    ),
    Option("list_at", "查询时@", "bool", ("查询at", "列表at"), hint="查活动时是否 @ 未完成的人"),
    Option("allow_add_others", "允许@加人", "bool", ("加人", "关注权限")),
    Option(
        "max_watch", "名单上限", "int", ("上限", "人数上限", "watch"), 1, 500, hint="本群最多几人"
    ),
    Option(
        "push_admin_only",
        "推送权限",
        "choice",
        ("权限", "谁可以开", "谁可以改"),
        choices=("群管", "所有人"),
    ),
)

GLOBAL_OPTIONS: tuple = (
    Option("cache_ttl", "缓存秒数", "int", ("缓存", "cache"), 60, 86400, scope="global"),
    Option("refresh_interval", "刷新分钟", "int", ("刷新", "refresh"), 0, 1440, scope="global"),
    Option("api_base", "数据源", "url", ("数据源", "源", "api"), scope="global"),
)

ALL_OPTIONS: tuple = GROUP_OPTIONS + GLOBAL_OPTIONS

_TRUE = {"开", "开启", "启用", "是", "true", "on", "yes", "y", "1", "打开"}
_FALSE = {"关", "关闭", "停用", "否", "false", "off", "no", "n", "0", "关掉"}


def parse_bool(text: Any) -> Optional[bool]:
    if isinstance(text, bool):
        return text
    cleaned = str(text).strip().lower()
    if cleaned in _TRUE:
        return True
    if cleaned in _FALSE:
        return False
    return None


def parse_int(text: Any) -> Optional[int]:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def parse_clock(text: Any) -> Optional[tuple]:
    """``21:30`` -> (21, 30)。"""

    cleaned = str(text or "").strip().replace("：", ":").replace(".", ":")
    if not cleaned:
        return None
    parts = cleaned.split(":")
    if len(parts) == 1:
        parts.append("0")
    if len(parts) != 2:
        return None
    hour, minute = parse_int(parts[0]), parse_int(parts[1])
    if hour is None or minute is None:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def format_clock(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def find_group_option(text: str) -> Optional[Option]:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        return None
    for option in GROUP_OPTIONS:
        if cleaned == option.key.lower() or cleaned == option.label:
            return option
        if cleaned in {alias.lower() for alias in option.aliases}:
            return option
    return None


def find_global_option(text: str) -> Optional[Option]:
    cleaned = (text or "").strip().lower()
    for option in GLOBAL_OPTIONS:
        if cleaned == option.key.lower() or cleaned == option.label:
            return option
        if cleaned in {alias.lower() for alias in option.aliases}:
            return option
    return None


def coerce_option(option: Option, raw: Any) -> tuple:
    """把用户输入解析成存储值，返回 (ok, value_or_error)。"""

    text = str(raw).strip()

    if option.kind == "bool":
        value = parse_bool(text)
        if value is None:
            return False, "只能填「开」或「关」"
        return True, value

    if option.kind == "int":
        value = parse_int(text)
        if value is None:
            return False, "要填数字"
        if not (option.minimum <= value <= option.maximum):
            return False, f"取值范围是 {option.minimum} ~ {option.maximum}"
        return True, value

    if option.kind == "time":
        parsed = parse_clock(text)
        if not parsed:
            return False, "时间格式不对，比如 21:30"
        return True, format_clock(*parsed)

    if option.kind == "choice":
        cleaned = text.lower()
        if cleaned in {"群管", "管理员", "管理", "admin", "owner"}:
            return True, True if option.key == "push_admin_only" else cleaned
        if cleaned in {"所有人", "全员", "普通群友", "all", "everyone", "member"}:
            return True, False if option.key == "push_admin_only" else cleaned
        return False, "只能填 " + " / ".join(option.choices)

    if option.kind == "url":
        if not text.startswith(("http://", "https://")):
            return False, "要以 http:// 或 https:// 开头"
        return True, text.rstrip("/")

    return False, "未知配置项"


@dataclass
class GroupSettings:
    push_enabled: bool = False
    hour: int = 21
    minute: int = 0
    remind_days: int = 3
    max_events: int = 20
    max_at_users: int = 15
    max_at_lines: int = 12
    list_at: bool = False
    allow_add_others: bool = True
    max_watch: int = 50
    push_admin_only: bool = True

    @property
    def push_time(self) -> str:
        return format_clock(self.hour, self.minute)


@dataclass
class GlobalSettings:
    cache_ttl: int = 1800
    refresh_interval: int = 60
    api_base: str = "https://starrailassistant.top/api/v1/activity"


class SettingsResolver:
    """把「群设置 → 全局设置 → .env 默认值」合成一份可用的设置。"""

    def __init__(self, store: DataStore, config: Config) -> None:
        self.store = store
        self.config = config

    # ------------------------------------------------------------------ 默认值
    def default(self, key: str) -> Any:
        cfg = self.config
        table = {
            "push_enabled": cfg.hoyo_events_push_default_enabled,
            "push_time": cfg.hoyo_events_push_time,
            "remind_days": cfg.hoyo_events_remind_days,
            "max_events": cfg.hoyo_events_max_events,
            "max_at_users": cfg.hoyo_events_max_at_users,
            "max_at_lines": cfg.hoyo_events_max_at_lines,
            "list_at": cfg.hoyo_events_list_at,
            "allow_add_others": cfg.hoyo_events_allow_add_others,
            "max_watch": cfg.hoyo_events_max_watch,
            "push_admin_only": cfg.hoyo_events_push_admin_only,
            "cache_ttl": cfg.hoyo_events_cache_ttl,
            "refresh_interval": cfg.hoyo_events_refresh_interval,
            "api_base": cfg.hoyo_events_api_base,
        }
        return table.get(key)

    # ------------------------------------------------------------------ 全局
    def global_raw(self, key: str) -> Any:
        settings = self.store.data.get("settings")
        if isinstance(settings, dict) and key in settings:
            return settings[key]
        return None

    def global_value(self, key: str) -> Any:
        raw = self.global_raw(key)
        if raw is not None:
            return raw
        default = self.default(key)
        if key == "push_time":
            hour, minute = parse_clock(default) or (21, 0)
            return format_clock(hour, minute)
        return default

    def set_global(self, key: str, value: Any) -> None:
        settings = self.store.data.setdefault("settings", {})
        settings[key] = value

    def global_settings(self) -> GlobalSettings:
        return GlobalSettings(
            cache_ttl=int(self.global_value("cache_ttl") or 1800),
            refresh_interval=int(self.global_value("refresh_interval") or 0),
            api_base=str(self.global_value("api_base") or ""),
        )

    def is_global_overridden(self, key: str) -> bool:
        return self.global_raw(key) is not None

    # ------------------------------------------------------------------ 群
    def group_value(self, group_id: Optional[int], key: str) -> Any:
        if group_id is not None:
            record = self.store.group(group_id)
            if key in record:
                return record[key]
            # 兼容旧字段
            if key == "push_time" and ("hour" in record or "minute" in record):
                hour = int(record.get("hour", 21) or 0)
                minute = int(record.get("minute", 0) or 0)
                return format_clock(hour, minute)
            if key == "push_enabled" and "enabled" in record:
                return bool(record["enabled"])
        return self.default(key)

    def group(self, group_id: Optional[int]) -> GroupSettings:
        hour, minute = parse_clock(self.group_value(group_id, "push_time")) or (21, 0)
        return GroupSettings(
            push_enabled=bool(parse_bool(self.group_value(group_id, "push_enabled"))),
            hour=hour,
            minute=minute,
            remind_days=int(self.group_value(group_id, "remind_days") or 0),
            max_events=int(self.group_value(group_id, "max_events") or 1),
            max_at_users=int(self.group_value(group_id, "max_at_users") or 1),
            max_at_lines=int(self.group_value(group_id, "max_at_lines") or 1),
            list_at=bool(parse_bool(self.group_value(group_id, "list_at"))),
            allow_add_others=bool(parse_bool(self.group_value(group_id, "allow_add_others"))),
            max_watch=int(self.group_value(group_id, "max_watch") or 1),
            push_admin_only=bool(parse_bool(self.group_value(group_id, "push_admin_only"))),
        )

    def set_group(self, group_id: int, key: str, value: Any) -> None:
        self.store.update_group(group_id, **{key: value})

    def is_group_overridden(self, group_id: int, key: str) -> bool:
        return key in self.store.group(group_id) or (
            key == "push_time" and ("hour" in self.store.group(group_id))
        )

    # ------------------------------------------------------------------ 生效
    def apply_to_source(self, source: Any) -> None:
        """把全局设置同步到数据源对象上（改了立刻生效）。"""

        current = self.global_settings()
        source.ttl = current.cache_ttl
        source.api_base = current.api_base.rstrip("/")

    # ------------------------------------------------------------------ 展示
    def describe(self, group_id: Optional[int]) -> str:
        from .formatter import render_settings

        rows = []
        for option in GROUP_OPTIONS:
            value = self.group_value(group_id, option.key)
            rows.append(
                (
                    option.label,
                    format_value(option, value),
                    self.is_group_overridden(group_id, option.key),
                    option.hint,
                )
            )
        global_rows = []
        for option in GLOBAL_OPTIONS:
            value = self.global_value(option.key)
            global_rows.append(
                (
                    option.label,
                    format_value(option, value),
                    self.is_global_overridden(option.key),
                    option.hint,
                )
            )
        return render_settings(rows, global_rows, self.group(group_id).push_time)

    def help_text(self) -> str:
        from .formatter import render_settings_help

        rows = [
            (option.label, option.kind, option.hint, option.aliases) for option in GROUP_OPTIONS
        ]
        return render_settings_help(rows)


def format_value(option: Option, value: Any) -> str:
    if option.kind == "bool":
        return "开" if parse_bool(value) else "关"
    if option.kind == "choice" and option.key == "push_admin_only":
        return "群管" if parse_bool(value) else "所有人"
    if value is None or value == "":
        return "未设置"
    return str(value)
