from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Game:
    key: str
    """数据源文件名，例如 ``ys``。"""

    name: str
    """全名，用于展示。"""

    short: str
    """简称，用于列表头与匹配。"""

    emoji: str
    aliases: tuple = ()
    """用户可能输入的名字（小写）。"""

    @property
    def slug(self) -> str:
        return self.key


GAMES: tuple = (
    Game(
        key="ys",
        name="原神",
        short="原神",
        emoji="🌏",
        aliases=("原神", "ys", "gi", "genshin", "genshinimpact", "提瓦特"),
    ),
    Game(
        key="sr",
        name="崩坏：星穹铁道",
        short="星铁",
        emoji="🚄",
        aliases=(
            "星铁",
            "星穹铁道",
            "崩坏星穹铁道",
            "崩坏：星穹铁道",
            "铁道",
            "sr",
            "hsr",
            "starrail",
            "崩铁",
        ),
    ),
    Game(
        key="zzz",
        name="绝区零",
        short="绝区零",
        emoji="📺",
        aliases=("绝区零", "绝区", "zzz", "zenless", "zenlesszonezero", "绝"),
    ),
)

GAME_KEYS: tuple = tuple(game.key for game in GAMES)
GAME_BY_KEY: dict = {game.key: game for game in GAMES}


def resolve_game(text: str) -> Optional[Game]:
    """把用户输入解析成游戏对象。"""

    if not text:
        return None
    cleaned = text.strip().lower().replace(" ", "")
    if not cleaned:
        return None
    for game in GAMES:
        if cleaned == game.key or cleaned == game.name or cleaned == game.short:
            return game
        if cleaned.lower() in game.aliases:
            return game
    # 兜底：包含关系，例如「原神活动」
    for game in GAMES:
        for alias in (game.key, game.name, game.short, *game.aliases):
            if alias and alias in cleaned:
                return game
    return None


def game_name(key: str) -> str:
    game = GAME_BY_KEY.get(key)
    return game.name if game else key


def game_display(key: str) -> str:
    game = GAME_BY_KEY.get(key)
    return f"{game.emoji}{game.short}" if game else key


def game_order(key: str) -> int:
    for index, game in enumerate(GAMES):
        if game.key == key:
            return index
    return len(GAMES)
