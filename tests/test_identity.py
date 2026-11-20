from __future__ import annotations

from datetime import timedelta

from nonebot_plugin_hoyo_events.datasource import ActivitySource
from nonebot_plugin_hoyo_events.models import Activity, now
from nonebot_plugin_hoyo_events.store import DataStore


def _source(store: DataStore, tmp_path) -> ActivitySource:
    return ActivitySource(
        store=store,
        api_base="https://example.invalid/api",
        timeout=1,
        ttl=60,
        override_path=tmp_path / "override.json",
    )


def _activity(name: str, start_days: int, end_days: int) -> Activity:
    at = now()
    return Activity(
        uid="",
        game="ys",
        name=name,
        start=at + timedelta(days=start_days),
        end=at + timedelta(days=end_days),
    )


def test_first_time_gets_generation_one(store, tmp_path):
    source = _source(store, tmp_path)
    activity = source.resolve_identity(_activity("幽境危战", -1, 10))
    assert activity.uid.endswith("#1")
    assert activity.generation == 1
    assert store.registry_entry(activity.base_id)["generation"] == 1


def test_refresh_keeps_same_uid(store, tmp_path):
    source = _source(store, tmp_path)
    first = source.resolve_identity(_activity("幽境危战", -1, 10))
    second = source.resolve_identity(_activity("幽境危战", -1, 10))
    assert first.uid == second.uid


def test_extension_keeps_uid_so_progress_is_kept(store, tmp_path):
    source = _source(store, tmp_path)
    first = source.resolve_identity(_activity("幽境危战", -1, 10))
    extended = source.resolve_identity(_activity("幽境危战", -1, 17))
    assert extended.uid == first.uid
    assert extended.generation == 1


def test_rerun_after_ended_bumps_generation(store, tmp_path):
    source = _source(store, tmp_path)
    at = now()
    first = source.resolve_identity(_activity("复刻活动", -120, -100), at=at - timedelta(days=90))
    rerun = source.resolve_identity(_activity("复刻活动", -1, 20), at=at)
    assert rerun.uid != first.uid
    assert rerun.generation == 2


def test_ended_event_still_in_feed_does_not_bump(store, tmp_path):
    """数据源里还会返回已经结束的活动，多次拉取不能一直换代。"""

    source = _source(store, tmp_path)
    at = now()
    first = source.resolve_identity(_activity("已结束的活动", -30, -10), at=at)
    again = source.resolve_identity(_activity("已结束的活动", -30, -10), at=at)
    assert first.uid == again.uid


def test_generation_isolated_per_game(store, tmp_path):
    source = _source(store, tmp_path)
    ys = source.resolve_identity(_activity("同名活动", -1, 10))
    other = Activity(uid="", game="sr", name="同名活动", start=ys.start, end=ys.end)
    sr = source.resolve_identity(other)
    assert ys.uid != sr.uid
    assert sr.uid.startswith("sr.")
