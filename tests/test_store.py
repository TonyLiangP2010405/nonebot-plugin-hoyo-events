from __future__ import annotations

import json

import pytest

from nonebot_plugin_hoyo_events.store import DataStore


def test_mark_done_is_idempotent(store: DataStore):
    assert store.mark_done("group_1", 100, ["a#1", "b#1"]) == ["a#1", "b#1"]
    assert store.mark_done("group_1", 100, ["a#1"]) == []
    assert store.done_uids("group_1", 100) == {"a#1", "b#1"}


def test_completion_isolated_between_users(store: DataStore):
    store.mark_done("group_1", 100, ["a#1"])
    assert store.done_uids("group_1", 101) == set()


def test_completion_isolated_between_groups(store: DataStore):
    store.mark_done("group_1", 100, ["a#1"])
    assert store.done_uids("group_2", 100) == set()


def test_completion_isolated_between_events(store: DataStore):
    store.mark_done("group_1", 100, ["a#1"])
    assert "b#1" not in store.done_uids("group_1", 100)


def test_unmark(store: DataStore):
    store.mark_done("group_1", 100, ["a#1", "b#1"])
    assert store.unmark_done("group_1", 100, ["a#1"]) == ["a#1"]
    assert store.done_uids("group_1", 100) == {"b#1"}
    assert store.unmark_done("group_1", 100, ["a#1"]) == []


def test_group_settings(store: DataStore):
    record = store.update_group(123, enabled=True, hour=22, minute=30)
    assert record["enabled"] is True
    assert store.group(123)["hour"] == 22

    store.mute(123, 456)
    assert store.muted(123) == {456}
    store.mute(123, 456)
    assert store.muted(123) == {456}
    store.unmute(123, 456)
    assert store.muted(123) == set()


def test_players(store: DataStore):
    store.touch_player("group_1", 100, "小明")
    store.touch_player("group_1", 100, "小明明")
    assert store.players("group_1")["100"]["nick"] == "小明明"
    assert len(store.players("group_1")) == 1


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "store.json"
    first = DataStore(path)
    first.mark_done("group_1", 100, ["a#1"])
    first.update_group(1, enabled=True)
    first.set_snapshot("ys", {"activities": [], "version": "7.2"})

    import asyncio

    asyncio.run(first.commit())

    second = DataStore(path)
    asyncio.run(second.load())
    assert second.done_uids("group_1", 100) == {"a#1"}
    assert second.group(1)["enabled"] is True
    assert second.snapshot("ys")["version"] == "7.2"


def test_corrupt_file_is_backed_up(tmp_path):
    path = tmp_path / "store.json"
    path.write_text("{ not json", "utf-8")
    store = DataStore(path)

    import asyncio

    asyncio.run(store.load())
    assert store.data["completions"] == {}
    assert list(tmp_path.glob("store.broken-*.json"))


def test_cleanup_removes_unknown_uids(store: DataStore):
    store.set_registry_entry("ys.aaa", {"generation": 1, "start": "", "end": ""})
    store.mark_done("group_1", 100, ["ys.aaa#1", "ys.zzz#9"])
    store.cleanup()
    assert store.done_uids("group_1", 100) == {"ys.aaa#1"}


def test_file_written_as_utf8_json(tmp_path):
    path = tmp_path / "store.json"
    store = DataStore(path)
    store.touch_player("group_1", 1, "中文昵称")

    import asyncio

    asyncio.run(store.commit())
    payload = json.loads(path.read_text("utf-8"))
    assert payload["players"]["group_1"]["1"]["nick"] == "中文昵称"


@pytest.mark.parametrize("scope", ["group_1", "private_1"])
def test_scope_keys_are_independent(store: DataStore, scope):
    store.mark_done(scope, 100, ["a#1"])
    assert store.done_uids(scope, 100) == {"a#1"}
