from __future__ import annotations

import os
import re
from functools import lru_cache
from io import BytesIO
from itertools import groupby
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .formatter import LINE, MAX_LINES, REMINDER_FOOTER, personal_reminder_row
from .games import GAME_BY_KEY
from .models import View, fmt_datetime

WIDTH = 800
MARGIN = 32
PANEL = WIDTH - MARGIN * 2
INK = "#1e293b"
MUTED = "#475569"
ACCENT = "#6249a2"
REMINDER_BACKGROUND = Path(__file__).with_name("assets") / "peach-meadow.png"
REMINDER_MIN_HEIGHT = 1200
REMINDER_HEADER_HEIGHT = 410
AVATAR_SIZE = 62
AVATAR_CENTER = (61, 132)
REMINDER_PAGE_SIZE = 24
GAME_COLORS = {
    "ys": ("#086b59", "#e8f7f1"),
    "sr": ("#59429a", "#f0ebfa"),
    "zzz": ("#87540b", "#fcf1db"),
}
FONT_PATHS = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"),
)
_ICONS = re.compile(r"[🎮📢📌📋⚙🌐✅⚠↩🎉⏰\ufe0f]")


@lru_cache(maxsize=8)
def _font_path(configured: str) -> str | None:
    for path in (configured, *FONT_PATHS):
        if not path:
            continue
        try:
            font = ImageFont.truetype(str(Path(path).expanduser()), 24)
        except OSError:
            continue
        if bytes(font.getmask("活")) != bytes(font.getmask("\u0378")):
            return str(Path(path).expanduser())
    return None


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int, limit: int) -> list[str]:
    # 按像素折行，中文和无空格的长 URL 也能正确换行。
    lines: list[str] = []
    line = ""
    for char in text:
        if font.getlength(line + char) <= width:
            line += char
            continue
        if len(lines) == limit - 1:
            while font.getlength(line + "…") > width:
                line = line[:-1]
            return lines + [line.rstrip() + "…"]
        lines.append(line.rstrip())
        line = char.lstrip()
    return lines + [line.rstrip()]


class _Card:
    """把头部、各内容区块和提示行拼到同一张图。"""

    def __init__(self, font_path: str) -> None:
        self.font_path = font_path
        self.fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self.panels: list[Image.Image] = []

    def font(self, size: int) -> ImageFont.FreeTypeFont:
        if size not in self.fonts:
            self.fonts[size] = ImageFont.truetype(self.font_path, size)
        return self.fonts[size]

    def panel(self, height: int, color: str = "#ffffff") -> ImageDraw.ImageDraw:
        panel = Image.new("RGBA", (PANEL, height))
        draw = ImageDraw.Draw(panel)
        draw.rounded_rectangle((0, 0, PANEL - 1, height - 1), 24, fill=color)
        self.panels.append(panel)
        return draw

    def text(self, draw, text: str, x: int, y: int, size: int, color: str = INK) -> None:
        draw.text((x, y), text, font=self.font(size), fill=color, anchor="lt")

    def header(self, title: str, subtitle: str = "") -> None:
        title_lines = _wrap(title, self.font(38), PANEL - 64, 2)
        height = 62 + len(title_lines) * 48 + (25 if subtitle else 0)
        draw = self.panel(height, "#222a49")
        draw.rounded_rectangle((28, 20, 73, 25), 3, fill="#b4a4ff")
        for index, line in enumerate(title_lines):
            self.text(draw, line, 28, 40 + index * 48, 38, "#ffffff")
        if subtitle:
            self.text(draw, subtitle, 30, height - 40, 21, "#dbe2ff")

    def reminder_header(
        self, nick: str, subtitle: str, date: str, avatar_data: bytes | None
    ) -> None:
        title_lines = _wrap(f"{nick}的活动进度", self.font(29), 270, 2)
        draw = self.panel(REMINDER_HEADER_HEIGHT, "#ffffff00")
        self._reminder_avatar(draw, self.panels[-1], nick, avatar_data)
        self.text(draw, date, 29, 63, 22, "#315967")
        title_y = 106 if len(title_lines) == 1 else 98
        for index, line in enumerate(title_lines):
            self.text(draw, line, 103, title_y + index * 34, 29, "#163743")
        self.text(draw, subtitle, 104, title_y + len(title_lines) * 34 + 7, 18, "#315967")

    def _reminder_avatar(self, draw, panel: Image.Image, nick: str, data: bytes | None) -> None:
        x, y = AVATAR_CENTER
        radius = AVATAR_SIZE // 2
        draw.ellipse((x - radius - 4, y - radius - 4, x + radius + 4, y + radius + 4),
                     fill="#fffdf9")
        avatar = _decode_avatar(data)
        if avatar is None:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="#eac8c8")
            initial = _ICONS.sub("", nick).strip()[:1] or "友"
            draw.text((x, y), initial, font=self.font(38), fill="#6c4051", anchor="mm")
            return
        circle = Image.new("L", avatar.size)
        ImageDraw.Draw(circle).ellipse((0, 0, AVATAR_SIZE - 1, AVATAR_SIZE - 1), fill=255)
        panel.paste(avatar, (x - radius, y - radius), circle)

    def section(
        self, heading: str, rows: list[str], *, quiet: bool = False, accent: str = ACCENT,
        illustrated: bool = False,
    ) -> None:
        lines = [
            line for row in rows
            for line in _wrap(row, self.font(23), PANEL - 56, 3)
        ]
        height = 42 + len(lines) * 33 + (39 if heading else 0)
        fill = "#f7f4fc" if quiet else "#ffffff"
        if illustrated:
            fill = "#eff9f3df" if quiet else "#fffffff0"
        draw = self.panel(height, fill)
        y = 20
        if heading:
            self.text(draw, heading, 26, y, 27, accent)
            y += 39
        for line in lines:
            self.text(draw, line, 26, y, 23, MUTED if quiet else INK)
            y += 33

    def game(self, key: str, version: str, entries: list, view: View, remind_days: int) -> None:
        game = GAME_BY_KEY.get(key)
        color, tint = GAME_COLORS.get(key, (ACCENT, "#f1ecfa"))
        headings = _wrap(version, self.font(20), PANEL - 56, 2) if version else []
        right = PANEL - 26
        rows = []
        for entry in entries:
            remaining = entry.remaining(view.at)
            status = f"已完成 · {remaining}" if entry.done else remaining
            pill_width = int(self.font(19).getlength(status)) + 22
            lines = _wrap(entry.activity.name, self.font(25), right - pill_width - 105, 2)
            rows.append((entry, lines, status, pill_width, max(55, 14 + len(lines) * 32)))
        header_height = 72 + len(headings) * 27
        draw = self.panel(header_height + sum(row[-1] for row in rows) + 16)
        draw.rounded_rectangle((12, 12, PANEL - 13, header_height - 6), 16, fill=tint)
        self.text(draw, game.name if game else key, 28, 25, 29, color)
        for index, line in enumerate(headings):
            self.text(draw, line, 28, 65 + index * 27, 20, color)

        y = header_height
        for index, (entry, lines, status, pill_width, height) in enumerate(rows):
            if index:
                draw.line((27, y, PANEL - 27, y), fill="#edf0f5")
            centre = y + height // 2
            box = (25, centre - 17, 72, centre + 17)
            draw.rounded_rectangle(box, 9, fill=tint)
            draw.text((48, centre), f"{entry.index:02d}", font=self.font(20), fill=color, anchor="mm")
            for line_index, line in enumerate(lines):
                self.text(draw, line, 87, y + 15 + line_index * 32, 25)
            urgent = 0 <= entry.activity.days_left(view.at) <= remind_days
            status_color, status_bg = (
                ("#16664e", "#e4f5ec") if entry.done else
                ("#a43d16", "#fce9dd") if urgent else (MUTED, "#eff2f8")
            )
            draw.rounded_rectangle(
                (right - pill_width, centre - 17, right, centre + 17), 12, fill=status_bg
            )
            draw.text(
                (right - 11, centre), status, font=self.font(19), fill=status_color, anchor="rm"
            )
            y += height

    def png(self, *, illustrated: bool = False) -> bytes:
        gap = 16
        height = MARGIN * 2 + sum(panel.height for panel in self.panels) + gap * (len(self.panels) - 1)
        if illustrated:
            image = _meadow_background(max(height + 88, REMINDER_MIN_HEIGHT))
        else:
            background = ImageOps.colorize(Image.linear_gradient("L"), "#e3e9ff", "#f9e9ed")
            image = background.resize((WIDTH, height))
        y = MARGIN
        for panel in self.panels:
            image.paste(panel, (MARGIN, y), panel)
            y += panel.height + gap
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


@lru_cache(maxsize=1)
def _meadow_art() -> Image.Image:
    with Image.open(REMINDER_BACKGROUND) as source:
        return ImageOps.fit(source.convert("RGB"), (WIDTH, REMINDER_MIN_HEIGHT))


def _meadow_background(height: int) -> Image.Image:
    try:
        art = _meadow_art()
    except OSError:
        return ImageOps.colorize(Image.linear_gradient("L"), "#ccecf5", "#d9ebac").resize(
            (WIDTH, height)
        )
    if height == art.height:
        return art.copy()
    # 列表较长时只延展草地，顶部人物保持原比例。
    horizon = MARGIN + REMINDER_HEADER_HEIGHT
    image = Image.new("RGB", (WIDTH, height))
    image.paste(art.crop((0, 0, WIDTH, horizon)), (0, 0))
    field = art.crop((0, horizon, WIDTH, art.height))
    image.paste(field.resize((WIDTH, height - horizon), Image.Resampling.LANCZOS), (0, horizon))
    return image


def _new_card(font: str) -> _Card:
    path = _font_path(font)
    if not path:
        raise OSError("未找到中文字体，请安装 Noto Sans CJK 或设置 HOYO_EVENTS_CARD_FONT")
    return _Card(path)


def render_activity_card(
    view: View,
    title: str,
    remind_days: int,
    footer: str = "",
    progress: str = "",
    *,
    font: str = "",
) -> bytes:
    card = _new_card(font)
    shown = view.entries[:MAX_LINES]
    summary = f"{view.at.month} 月 {view.at.day} 日 · {len(view.entries)} 项活动"
    if progress:
        summary += f" · {progress}"
    card.header(title, summary)
    for key, entries in groupby(shown, key=lambda entry: entry.activity.game):
        card.game(key, view.headers.get(key, ""), list(entries), view, remind_days)
    if len(shown) < len(view.entries):
        card.section("", [f"还有 {len(view.entries) - len(shown)} 项未显示，可查询单个游戏。"], quiet=True)
    if view.upcoming:
        card.section(
            "即将开始",
            [
                f"{GAME_BY_KEY[act.game].short} · {act.name} · {fmt_datetime(act.start)}"
                for act in view.upcoming
            ],
        )
    if footer:
        card.section("", footer.splitlines(), quiet=True)
    return card.png()


def render_text_card(text: str, *, font: str = "") -> bytes:
    """复用帮助、设置等现有文案，按段落和小标题拆成区块。"""
    card = _new_card(font)
    title, _, body = _ICONS.sub("", text).strip().partition("\n")
    card.header(title.strip())
    heading = ""
    rows: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if line and line != LINE and not line.startswith("【"):
            rows.append(line)
            continue
        if rows:
            card.section(heading, rows)
            rows = []
        heading = line.strip("【】") if line.startswith("【") else ""
    if rows:
        card.section(heading, rows)
    return card.png()


def _decode_avatar(data: bytes | None) -> Image.Image | None:
    if not data:
        return None
    try:
        with Image.open(BytesIO(data), formats=("JPEG", "PNG", "GIF", "WEBP")) as image:
            if max(image.size) <= 2048:
                return ImageOps.fit(image.convert("RGB"), (AVATAR_SIZE, AVATAR_SIZE))
    except (OSError, ValueError, Image.DecompressionBombError):
        pass
    return None


def render_personal_reminder_cards(
    view: View, nick: str, avatar_data: bytes | None = None, *, font: str = ""
) -> list[bytes]:
    pending = [entry for entry in view.entries if not entry.done]
    completed = [entry for entry in view.entries if entry.done]
    entries = pending + completed
    pages = range(0, len(entries), REMINDER_PAGE_SIZE)
    images = []
    for number, start in enumerate(pages, start=1):
        card = _new_card(font)
        subtitle = f"未完成 {len(pending)} 项 · 已完成 {len(completed)} 项"
        if len(pages) > 1:
            subtitle += f" · {number}/{len(pages)}"
        card.reminder_header(nick, subtitle, f"{view.at.month} 月 {view.at.day} 日", avatar_data)
        page = entries[start:start + REMINDER_PAGE_SIZE]
        for done, group in groupby(page, key=lambda entry: entry.done):
            title, color = ("已完成", "#18644d") if done else ("未完成", "#a43d16")
            card.section(
                title, [personal_reminder_row(entry, view.at) for entry in group],
                accent=color, illustrated=True,
            )
        if not completed and number == len(pages):
            card.section("已完成", ["还没有标记完成的活动"], accent="#18644d", illustrated=True)
        card.section("", [REMINDER_FOOTER], quiet=True, illustrated=True)
        images.append(card.png(illustrated=True))
    return images
