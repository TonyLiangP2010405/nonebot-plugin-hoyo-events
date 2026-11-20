from __future__ import annotations

from datetime import datetime, timedelta

from nonebot_plugin_hoyo_events.models import (
    Activity,
    days_left,
    event_base_id,
    fmt_remaining,
    now,
    parse_datetime,
)


def test_parse_datetime_iso():
    dt = parse_datetime("2026-11-20T11:00:00")
    assert dt is not None
    assert dt.hour == 11
    assert dt.utcoffset() == timedelta(hours=8)


def test_parse_datetime_timezone_and_z():
    dt = parse_datetime("2026-11-20T03:00:00Z")
    assert dt is not None
    assert dt.astimezone().hour == 11
    dt2 = parse_datetime("2026-11-20T11:00:00+08:00")
    assert dt2 == dt


def test_parse_datetime_variants():
    assert parse_datetime("2026-11-20 11:00:00") is not None
    assert parse_datetime("2026/11/20 11:00") is not None
    assert parse_datetime(1732000000) is not None
    assert parse_datetime(None) is None
    assert parse_datetime("") is None
    assert parse_datetime("不是时间") is None
    assert parse_datetime(True) is None


def test_days_left_and_remaining():
    at = datetime(2026, 11, 20, 12, 0, tzinfo=now().tzinfo)
    assert days_left(at + timedelta(days=5), at) == 5
    assert days_left(at + timedelta(hours=2), at) == 0
    assert days_left(at - timedelta(hours=2), at) == 0

    assert fmt_remaining(at + timedelta(days=5), at) == "剩 5 天"
    assert fmt_remaining(at + timedelta(hours=3), at) == "今天结束"
    assert fmt_remaining(at - timedelta(hours=1), at) == "已结束"


def test_event_base_id_is_stable():
    a = event_base_id("ys", "幽境危战")
    b = event_base_id("ys", " 幽境危战 ")
    c = event_base_id("sr", "幽境危战")
    assert a == b
    assert a != c
    assert a.startswith("ys.")


def test_activity_roundtrip():
    at = now()
    activity = Activity(
        uid="ys.abc#2",
        game="ys",
        name="幽境危战",
        start=at,
        end=at + timedelta(days=3),
        description="描述",
        generation=2,
    )
    payload = activity.to_dict()
    restored = Activity.from_dict(payload)
    assert restored is not None
    assert restored.uid == activity.uid
    assert restored.name == activity.name
    assert restored.generation == 2
    assert restored.base_id == "ys.abc"
    assert restored.is_active(at)
    assert not restored.is_over(at)


def test_activity_from_invalid_dict():
    assert Activity.from_dict(None) is None
    assert Activity.from_dict({}) is None
    assert Activity.from_dict({"name": "无时间"}) is None
    assert Activity.from_dict({"name": "x", "start": "2026-01-01", "end": "bad"}) is None
