from __future__ import annotations

import json

import pytest

from nonebot_plugin_hoyo_events import datasource
from nonebot_plugin_hoyo_events.datasource import ActivitySource, SourceError, parse_payload
from nonebot_plugin_hoyo_events.store import DataStore

PAYLOAD = {
    "version": "4.7",
    "versionName": "此刻，将群星付之一笑",
    "startTime": "2026-11-11T11:00:00",
    "endTime": "2026-12-23T06:00:00",
    "cover": "https://example.com/a.jpg",
    "activities": [
        {
            "name": "开场好礼先抱走",
            "kind": "其他活动",
            "description": "好戏开场",
            "startTime": "2026-11-11T11:00:00",
            "endTime": "2026-12-23T05:59:59",
        },
        {
            "name": "奇库寻遗",
            "description": "寻遗",
            "startTime": "2026-11-11T11:00:00",
            "endTime": "2026-12-23T03:59:59",
        },
        {"name": "坏数据", "startTime": "坏", "endTime": "坏"},
        {"name": "", "startTime": "2026-11-11T11:00:00", "endTime": "2026-12-01T00:00:00"},
    ],
}


def test_parse_payload():
    meta, activities, dropped = parse_payload("sr", PAYLOAD)
    assert meta["version"] == "4.7"
    assert meta["version_name"] == "此刻，将群星付之一笑"
    assert len(activities) == 2
    assert dropped == 2
    # 按结束时间排序
    assert activities[0].name == "奇库寻遗"
    assert activities[0].game == "sr"


def test_parse_payload_rejects_bad_shape():
    with pytest.raises(SourceError):
        parse_payload("sr", [])
    with pytest.raises(SourceError):
        parse_payload("sr", {"version": "1.0"})


def test_parse_payload_dedupes_same_name():
    payload = {
        "activities": [
            {"name": "同名", "startTime": "2026-11-01T00:00:00", "endTime": "2026-11-10T00:00:00"},
            {"name": "同名", "startTime": "2026-11-01T00:00:00", "endTime": "2026-11-20T00:00:00"},
        ]
    }
    _, activities, _ = parse_payload("ys", payload)
    assert len(activities) == 1
    assert activities[0].end.day == 20


def _source(store: DataStore, tmp_path) -> ActivitySource:
    return ActivitySource(
        store=store,
        api_base="https://example.invalid/api",
        timeout=1,
        ttl=60,
        override_path=tmp_path / "override.json",
    )


@pytest.mark.asyncio
async def test_refresh_success(store, tmp_path, monkeypatch):
    source = _source(store, tmp_path)
    calls = []

    async def fake_get(url, timeout):
        calls.append(url)
        return PAYLOAD

    monkeypatch.setattr(datasource, "_http_get_json", fake_get)
    results = await source.refresh(games=["sr"])

    assert results["sr"].ok
    assert results["sr"].count == 2
    assert calls == ["https://example.invalid/api/sr.json"]
    activities = source.activities("sr")
    assert {item.name for item in activities} == {"开场好礼先抱走", "奇库寻遗"}
    assert all(item.uid.endswith("#1") for item in activities)
    assert store.snapshot("sr")["version"] == "4.7"
    assert source.snapshot_age("sr") is not None


@pytest.mark.asyncio
async def test_refresh_failure_keeps_cache(store, tmp_path, monkeypatch):
    source = _source(store, tmp_path)

    async def ok_get(url, timeout):
        return PAYLOAD

    monkeypatch.setattr(datasource, "_http_get_json", ok_get)
    await source.refresh(games=["sr"])

    async def bad_get(url, timeout):
        raise SourceError("boom")

    monkeypatch.setattr(datasource, "_http_get_json", bad_get)
    results = await source.refresh(games=["sr"], force=True)

    assert results["sr"].ok is False
    assert results["sr"].stale is True
    assert len(source.activities("sr")) == 2
    assert "boom" in store.snapshot("sr")["last_error"]


@pytest.mark.asyncio
async def test_ttl_skips_network(store, tmp_path, monkeypatch):
    source = _source(store, tmp_path)
    calls = []

    async def fake_get(url, timeout):
        calls.append(url)
        return PAYLOAD

    monkeypatch.setattr(datasource, "_http_get_json", fake_get)
    await source.refresh(games=["sr"])
    await source.refresh(games=["sr"], respect_ttl=True)
    assert len(calls) == 1
    await source.refresh(games=["sr"], respect_ttl=True, force=True)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_override_add_patch_remove(store, tmp_path, monkeypatch):
    source = _source(store, tmp_path)

    async def fake_get(url, timeout):
        return PAYLOAD

    monkeypatch.setattr(datasource, "_http_get_json", fake_get)

    override = {
        "sr": {
            "remove": ["奇库寻遗"],
            "patch": [{"name": "开场好礼先抱走", "endTime": "2027-01-01T00:00:00"}],
            "add": [
                {
                    "name": "手动添加的活动",
                    "startTime": "2026-11-01T00:00:00",
                    "endTime": "2026-12-01T00:00:00",
                }
            ],
        }
    }
    (tmp_path / "override.json").write_text(json.dumps(override, ensure_ascii=False), "utf-8")

    await source.refresh(games=["sr"])
    activities = {item.name: item for item in source.activities("sr")}
    assert set(activities) == {"开场好礼先抱走", "手动添加的活动"}
    assert activities["开场好礼先抱走"].end.year == 2027


@pytest.mark.asyncio
async def test_broken_override_is_ignored(store, tmp_path, monkeypatch):
    source = _source(store, tmp_path)
    (tmp_path / "override.json").write_text("{broken", "utf-8")

    async def fake_get(url, timeout):
        return PAYLOAD

    monkeypatch.setattr(datasource, "_http_get_json", fake_get)
    await source.refresh(games=["sr"])
    assert len(source.activities("sr")) == 2


@pytest.mark.asyncio
async def test_refresh_all_failure_without_cache(store, tmp_path, monkeypatch):
    source = _source(store, tmp_path)

    async def bad_get(url, timeout):
        raise SourceError("网络不通")

    monkeypatch.setattr(datasource, "_http_get_json", bad_get)
    results = await source.refresh(games=["ys"])
    assert results["ys"].ok is False
    assert results["ys"].stale is False
    assert source.activities("ys") == []


def test_describe_age():
    from nonebot_plugin_hoyo_events.datasource import describe_age

    assert describe_age(None) == "未更新"
    assert describe_age(10) == "刚刚"
    assert describe_age(600) == "10 分钟前"
    assert describe_age(7200) == "2 小时前"
    assert describe_age(90000) == "1 天前"
