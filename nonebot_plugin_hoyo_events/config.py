from __future__ import annotations

from pydantic import BaseModel, Field


class Config(BaseModel):
    """插件配置，全部带默认值：缺失时不会导致 import 失败。"""

    hoyo_events_enabled: bool = True
    """是否启用插件。"""

    hoyo_events_api_base: str = "https://starrailassistant.top/api/v1/activity"
    """活动数据源前缀，插件会请求 ``{base}/{游戏}.json``（游戏为 ys / sr / zzz）。"""

    hoyo_events_cache_ttl: int = Field(default=1800, ge=60)
    """查询时的数据缓存时长（秒）。超过后会先拉取最新数据再回复。"""

    hoyo_events_refresh_interval: int = Field(default=60, ge=0)
    """后台自动刷新间隔（分钟），设为 0 关闭后台刷新。"""

    hoyo_events_push_time: str = "21:00"
    """群默认每日推送时间，格式 HH:MM。"""

    hoyo_events_push_default_enabled: bool = False
    """新群是否默认开启每日推送（需要群管手动开启时为 False）。"""

    hoyo_events_remind_days: int = Field(default=3, ge=0, le=30)
    """剩余天数小于等于该值时，活动会被标记为「即将结束」并加急提醒。"""

    hoyo_events_list_at: bool = False
    """群友主动查询活动时，是否默认 @ 未完成的群友。"""

    hoyo_events_max_events: int = Field(default=20, ge=1, le=60)
    """每个游戏最多展示多少条活动。"""

    hoyo_events_max_at_users: int = Field(default=15, ge=1, le=50)
    """每条活动最多 @ 多少人，避免刷屏。"""

    hoyo_events_max_at_lines: int = Field(default=12, ge=1, le=50)
    """推送时最多为几个活动单独列 @ 名单，多出来的合并成一行。"""

    hoyo_events_http_timeout: float = Field(default=15.0, gt=0)
    """请求数据源的超时时间（秒）。"""
