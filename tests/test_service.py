from __future__ import annotations

from datetime import timedelta

import pytest

from nonebot_plugin_hoyo_events.config import Config
from nonebot_plugin_hoyo_events.models import Activity, now
from nonebot_plugin_hoyo_events.service import (
    ActivityService,
    build_scope_key,
    build_view_key,
    scope_of_private,
)


class FakeSource:
    def __init__(self, mapping: dict) -> None:
        self.mapping = mapping

    def activities(self, game, at=None):
        return list(self.mapping.get(game, []))

    def data_age_text(self) -> str:
        return "刚刚更新"

    def snapshot_age(self, game):
        return 0.0

    async def ensure_fresh(self, force: bool = False) -> dict:
        return {}

    def refresh_summary(self, results: dict) -> str:
        return ""


def _act(name: str, game: str, end_days: int, uid: str, start_days: int = -1) -> Activity:
    at = now()
    return Activity(
        uid=uid,
        game=game,
        name=name,
        start=at + timedelta(days=start_days),
        end=at + timedelta(days=end_days),
    )


@pytest.fixture
def service(store) -> ActivityService:
    mapping = {
        "ys": [
            _act("幽境危战", "ys", 5, "ys.a#1"),
            _act("砺行修远", "ys", 60, "ys.b#1"),
            _act("刚结束的", "ys", -1, "ys.c#1"),
        ],
        "sr": [
            _act("虚构叙事", "sr", 10, "sr.a#1"),
            _act("混沌回忆", "sr", 24, "sr.b#1"),
        ],
        "zzz": [_act("天使应援", "zzz", 8, "zzz.a#1")],
    }
    return ActivityService(store=store, source=FakeSource(mapping), config=Config())


async def _mark(service: ActivityService, scope: str, user: int, tokens, **kwargs):
    return await service.mark_completed(scope, user, tokens, **kwargs)


@pytest.mark.asyncio
async def test_view_numbering_is_continuous(service):
    view = service.build_view(None, "group_1", 100)
    assert [entry.index for entry in view.entries] == [1, 2, 3, 4, 5]
    assert [entry.activity.game for entry in view.entries] == ["ys", "ys", "sr", "sr", "zzz"]
    # 已结束的不进列表
    assert "刚结束的" not in [entry.activity.name for entry in view.entries]


@pytest.mark.asyncio
async def test_view_marks_done(service, store):
    store.mark_done("group_1", 100, ["ys.a#1"])
    view = service.build_view(None, "group_1", 100)
    done = {entry.activity.uid for entry in view.entries if entry.done}
    assert done == {"ys.a#1"}


@pytest.mark.asyncio
async def test_mark_by_index_uses_cached_view(service):
    view = service.build_view(None, "group_1", 100)
    service.remember_view(build_view_key(1, 100), view)

    outcome = await _mark(service, "group_1", 100, ["1"], view_keys=(build_view_key(1, 100),))
    assert [act.name for act in outcome.matched] == ["幽境危战"]
    assert outcome.newly == ["ys.a#1"]


@pytest.mark.asyncio
async def test_mark_by_name_and_fuzzy(service):
    outcome = await _mark(service, "group_1", 100, ["幽境危战"])
    assert outcome.newly == ["ys.a#1"]

    fuzzy = await _mark(service, "group_1", 101, ["混沌回亿"])
    assert [act.name for act in fuzzy.matched] == ["混沌回忆"]


@pytest.mark.asyncio
async def test_mark_by_game_and_all(service):
    outcome = await _mark(service, "group_1", 100, ["原神"])
    assert {act.uid for act in outcome.matched} == {"ys.a#1", "ys.b#1"}

    everything = await _mark(service, "group_1", 101, ["全部"])
    assert len(everything.matched) == 5


@pytest.mark.asyncio
async def test_mark_invalid_index(service):
    outcome = await _mark(service, "group_1", 100, ["99"], view_keys=(build_view_key(1, 100),))
    assert outcome.matched == []
    assert outcome.errors


@pytest.mark.asyncio
async def test_completion_is_per_user_per_event(service, store):
    """核心需求：完成活动 1 不影响活动 2 的 @。"""

    view = service.build_view(None, "group_1", 100)
    service.remember_view(build_view_key(1, 100), view)
    await _mark(service, "group_1", 100, ["1"], view_keys=(build_view_key(1, 100),))
    assert store.done_uids("group_1", 100) == {"ys.a#1"}

    pending = {act.uid for act in service.pending("group_1", 100)}
    assert "ys.a#1" not in pending
    assert "sr.a#1" in pending
    assert len(pending) == 4


def test_at_targets_skips_completed_per_event(service, store):
    store.touch_player("group_1", 100, "小明")
    store.touch_player("group_1", 101, "小红")
    store.mark_done("group_1", 100, ["ys.a#1"])

    activities = [act for act in service.active_activities() if act.game == "ys"]
    targets = service.at_targets("group_1", activities, 1)

    first = next(t for t in targets if t.activity.uid == "ys.a#1")
    second = next(t for t in targets if t.activity.uid == "ys.b#1")
    assert first.users == [101]
    assert second.users == [100, 101]


def test_at_targets_respects_mute(service, store):
    store.touch_player("group_1", 100, "小明")
    store.touch_player("group_1", 101, "小红")
    store.mute(1, 101)

    targets = service.at_targets("group_1", service.active_activities("ys"), 1)
    assert all(101 not in target.users for target in targets)
    assert all(100 in target.users for target in targets)


def test_at_targets_caps_users(service, store):
    for uid in range(200, 260):
        store.touch_player("group_1", uid, f"u{uid}")
    targets = service.at_targets("group_1", service.active_activities("ys"), 1)
    assert targets
    assert all(len(target.users) <= service.config.hoyo_events_max_at_users for target in targets)
    assert all(target.total == 60 for target in targets)


def test_at_targets_empty_without_players(service):
    assert service.at_targets("group_1", service.active_activities(), 1) == []


@pytest.mark.asyncio
async def test_unmark(service, store):
    await _mark(service, "group_1", 100, ["幽境危战"])
    assert store.done_uids("group_1", 100) == {"ys.a#1"}
    outcome = await service.unmark_completed("group_1", 100, ["幽境危战"])
    assert outcome.removed == ["ys.a#1"]
    assert store.done_uids("group_1", 100) == set()


def test_pending_by_user(service, store):
    store.touch_player("group_1", 100, "小明")
    store.touch_player("group_1", 101, "小红")
    store.mark_done("group_1", 100, ["ys.a#1", "ys.b#1", "sr.a#1", "sr.b#1", "zzz.a#1"])

    pending = service.pending_by_user("group_1")
    assert 100 not in pending
    assert len(pending[101]) == 5


def test_scope_helpers():
    assert build_scope_key(None) == ""
    assert build_scope_key(123) == "g123"
    assert build_view_key(123, 456) == "g123:456"
    assert build_view_key(None, 456) == "p456"
    assert scope_of_private(456) == "private_456"


def test_view_expires(service, store):
    view = service.build_view(None, "group_1", 100)
    view.at = now() - timedelta(days=1)
    service.remember_view("g1:100", view)
    assert service.get_view("g1:100") is None
    assert service.view_by_index(1, "g1:100") is None
