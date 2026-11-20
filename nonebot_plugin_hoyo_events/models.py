from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# 国服统一使用 UTC+8，用固定偏移避免依赖 tzdata。
TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(
    r"[「」『』【】\[\]（）()<>《》\"'“”‘’·•・:：!！?？,，。.、~～—\-_=+*/\\|]"
)


def now() -> datetime:
    """当前时间（UTC+8）。"""

    return datetime.now(TZ)


def parse_datetime(value: Any) -> Optional[datetime]:
    """把数据源里的时间字段解析成带时区的 datetime，无法解析时返回 None。"""

    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=TZ)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), TZ)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    candidate = text
    if "T" not in candidate and " " in candidate:
        candidate = candidate.replace(" ", "T", 1)

    dt: Optional[datetime] = None
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d",
        ):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def iso(dt: Optional[datetime]) -> str:
    return dt.astimezone(TZ).isoformat() if dt else ""


def normalize_name(name: str) -> str:
    """活动名归一化：用于生成稳定 ID 与模糊匹配。"""

    text = unicodedata.normalize("NFKC", str(name or "")).strip().lower()
    text = _SPACE_RE.sub("", text)
    return text


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def event_base_id(game: str, name: str) -> str:
    """活动的基础 ID：同一游戏下的同名活动（含复刻）共享基础 ID，靠代次区分。"""

    normalized = normalize_name(name) or str(name or "").strip()
    return f"{game}.{short_hash(normalized)}"


def days_left(end: datetime, at: Optional[datetime] = None) -> int:
    """按自然日计算剩余天数（活动当天结束记为 0）。"""

    at = at or now()
    return (end.astimezone(TZ).date() - at.astimezone(TZ).date()).days


def fmt_date(dt: datetime) -> str:
    dt = dt.astimezone(TZ)
    return f"{dt.month}月{dt.day}日"


def fmt_md(dt: datetime) -> str:
    dt = dt.astimezone(TZ)
    return f"{dt.month:02d}-{dt.day:02d}"


def fmt_datetime(dt: datetime) -> str:
    dt = dt.astimezone(TZ)
    return f"{dt.month:02d}-{dt.day:02d} {dt:%H:%M}"


def fmt_remaining(end: datetime, at: Optional[datetime] = None) -> str:
    """把结束时间渲染成「剩 N 天 / 今天结束 / 已结束」。"""

    at = at or now()
    if end <= at:
        return "已结束"
    remaining = days_left(end, at)
    if remaining <= 0:
        return "今天结束"
    return f"剩 {remaining} 天"


@dataclass
class Activity:
    """一条游戏内活动。"""

    uid: str
    """全局唯一 ID，形如 ``ys.1a2b3c4d5e#1``，``#`` 后是代次，复刻活动会 +1。"""

    game: str
    """游戏标识：ys / sr / zzz。"""

    name: str
    start: datetime
    end: datetime
    description: str = ""
    kind: str = ""
    cover: str = ""
    generation: int = 1
    extra: dict = field(default_factory=dict)

    @property
    def base_id(self) -> str:
        return self.uid.split("#", 1)[0]

    def is_active(self, at: Optional[datetime] = None) -> bool:
        at = at or now()
        return self.start <= at <= self.end

    def is_upcoming(self, at: Optional[datetime] = None) -> bool:
        at = at or now()
        return self.start > at

    def is_over(self, at: Optional[datetime] = None) -> bool:
        at = at or now()
        return self.end < at

    def days_left(self, at: Optional[datetime] = None) -> int:
        return days_left(self.end, at)

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "game": self.game,
            "name": self.name,
            "start": iso(self.start),
            "end": iso(self.end),
            "description": self.description,
            "kind": self.kind,
            "cover": self.cover,
            "generation": self.generation,
            "extra": self.extra or {},
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional[Activity]:
        if not isinstance(data, dict):
            return None
        name = str(data.get("name") or "").strip()
        start = parse_datetime(data.get("start"))
        end = parse_datetime(data.get("end"))
        if not name or not start or not end:
            return None
        game = str(data.get("game") or "").strip()
        uid = str(data.get("uid") or "").strip()
        if not game:
            return None
        if not uid:
            uid = f"{event_base_id(game, name)}#1"
        try:
            generation = int(data.get("generation") or uid.rsplit("#", 1)[-1] or 1)
        except (TypeError, ValueError):
            generation = 1
        return cls(
            uid=uid,
            game=game,
            name=name,
            start=start,
            end=end,
            description=str(data.get("description") or ""),
            kind=str(data.get("kind") or ""),
            cover=str(data.get("cover") or ""),
            generation=generation,
            extra=dict(data.get("extra") or {}),
        )


@dataclass
class Entry:
    """列表中的一行。"""

    index: int
    activity: Activity
    done: bool = False

    @property
    def expired(self) -> bool:
        return self.activity.is_over()

    def remaining(self, at: Optional[datetime] = None) -> str:
        return fmt_remaining(self.activity.end, at)


@dataclass
class View:
    """一次渲染出来的活动列表，编号与缓存一一对应。"""

    at: datetime
    entries: list = field(default_factory=list)
    upcoming: list = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    title: str = ""

    @property
    def uids(self) -> list:
        return [entry.activity.uid for entry in self.entries]

    def activities(self) -> list:
        return [entry.activity for entry in self.entries]

    def by_index(self, index: int) -> Optional[Entry]:
        for entry in self.entries:
            if entry.index == index:
                return entry
        return None
