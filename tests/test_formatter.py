from __future__ import annotations

from datetime import timedelta

from nonebot_plugin_hoyo_events.formatter import (
    per_event_lines,
    per_person_lines,
    render_completion,
    render_help,
    render_push,
    render_status,
    render_target_line,
    render_view,
)
from nonebot_plugin_hoyo_events.models import Activity, Entry, View, now
from nonebot_plugin_hoyo_events.service import AtTarget, CompletionOutcome


def _view() -> View:
    at = now()
    ys = Activity("ys.a#1", "ys", "幽境危战", at - timedelta(days=2), at + timedelta(days=2))
    sr = Activity("sr.a#1", "sr", "虚构叙事", at - timedelta(days=1), at + timedelta(days=20))
    view = View(at=at, headers={"ys": "v7.2 坏灭肇始之时", "sr": "v4.7 此刻"})
    view.entries = [
        Entry(index=1, activity=ys, done=True),
        Entry(index=2, activity=sr, done=False),
    ]
    return view


def test_render_view_contains_key_info():
    text = render_view(_view(), remind_days=3)
    assert "【原神】" in text
    assert "【星铁】" in text
    assert "幽境危战" in text
    assert "剩 2 天" in text
    assert "⏰" in text  # 2 天内结束
    assert "✓" in text  # 已完成标记
    assert "1." in text and "2." in text


def test_render_view_empty():
    view = View(at=now())
    assert "没有进行中" in render_view(view)


def test_render_view_upcoming():
    at = now()
    view = View(at=at)
    view.entries = [
        Entry(
            index=1,
            activity=Activity(
                "ys.a#1", "ys", "进行中", at - timedelta(days=1), at + timedelta(days=5)
            ),
        )
    ]
    view.upcoming = [
        Activity("ys.b#1", "ys", "即将开始", at + timedelta(days=1), at + timedelta(days=9))
    ]
    text = render_view(view)
    assert "即将开始" in text
    assert "即将开始" in text


def test_render_push_with_targets():
    view = _view()
    activity = view.entries[0].activity
    targets = [AtTarget(activity=activity, users=[100, 101], total=2)]
    payload = render_push(view, targets=targets, style="event")
    assert payload.has_targets
    assert "还没完成的群友" in payload.head
    assert "已完成 编号" in payload.tail
    assert payload.lines[0].users == [100, 101]
    assert payload.lines[0].prefix.endswith("：")
    line = render_target_line(targets[0])
    assert "幽境危战" in line and line.endswith("：")


def test_render_push_without_players():
    payload = render_push(_view(), [], no_player_hint=True)
    assert "还没有人登记" in payload.tail


def test_render_completion():
    activity = Activity(
        "ys.a#1", "ys", "幽境危战", now() - timedelta(days=1), now() + timedelta(days=5)
    )
    outcome = CompletionOutcome(matched=[activity], newly=["ys.a#1"])
    text = render_completion(outcome, scope_pending=3, scope_total=5)
    assert "已记录完成" in text
    assert "幽境危战" in text
    assert "2/5" in text


def test_render_completion_errors():
    outcome = CompletionOutcome(errors=["没找到活动：xxx"])
    text = render_completion(outcome, scope_pending=0, scope_total=0)
    assert "没找到活动" in text


def test_render_status_and_help():
    status = render_status(True, "21:00", 5, "刚刚更新", 12, False)
    assert "已开启" in status
    help_text = render_help("21:00", 3)
    assert "已完成" in help_text
    assert "活动提醒" in help_text


def test_push_collapses_too_many_at_lines():
    view = _view()
    base = view.entries[0].activity
    targets = [AtTarget(activity=base, users=[uid], total=1) for uid in range(100, 100 + 15)]
    lines = per_event_lines(targets, max_at_lines=10)
    assert len(lines) == 11
    assert lines[-1].prefix == "· 其余 5 个活动："
    assert lines[-1].users == [110, 111, 112, 113, 114]


def test_per_person_lines():
    rows = [
        {"uid": 100, "done": [1, 2], "pending": [3, 4, 5]},
        {"uid": 200, "done": [], "pending": [1, 2, 3]},
    ]
    lines = per_person_lines(rows, max_at_users=10)
    assert [line.users for line in lines] == [[100], [200]]
    assert lines[0].suffix == "还差 3 个（已完成 1 2）"
    assert lines[1].suffix == "还差 3 个"


def test_per_person_lines_caps_users():
    rows = [{"uid": uid, "done": [], "pending": [1]} for uid in range(100, 120)]
    lines = per_person_lines(rows, max_at_users=15)
    assert len(lines) == 16
    assert "还有 5 人未完成" in lines[-1].prefix
