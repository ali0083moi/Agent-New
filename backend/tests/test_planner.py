from datetime import datetime

from app import planner, prefs
from app.db import Event, PlannedBlock, RecurringEvent, Task, WorkLog, session


def add_task(title, due=None, minutes=60, priority=2, **kw):
    with session() as s:
        t = Task(title=title, due_at=due, estimated_minutes=minutes, priority=priority, **kw)
        s.add(t)
        s.flush()
        return t.id


def test_plan_respects_classes_and_deadline_order():
    with session() as s:
        s.add(RecurringEvent(title="زبان", kind="language_class", weekdays=["mon"], start_time="12:00", end_time="14:00"))
    late = add_task("گزارش کار", due=datetime(2026, 10, 25, 18), minutes=120)
    early = add_task("تکلیف زبان", due=datetime(2026, 10, 20, 9), minutes=90)
    with session() as s:
        res = planner.plan(s, days=7)
    assert res.blocks, "should plan something"
    first = res.blocks[0]
    assert first["task_id"] == early  # earliest deadline first
    # nothing overlaps the class (+30 min buffer): 11:30-14:30 on Monday
    for b in res.blocks:
        st, en = datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])
        assert not (st < datetime(2026, 10, 19, 14, 30) and en > datetime(2026, 10, 19, 11, 30))
    total = {early: 0, late: 0}
    for b in res.blocks:
        total[b["task_id"]] += b["minutes"]
    assert total[early] == 90 and total[late] == 120
    assert not res.warnings


def test_plan_warns_when_impossible():
    add_task("پروژه بزرگ", due=datetime(2026, 10, 19, 15), minutes=600)
    with session() as s:
        res = planner.plan(s, days=3)
    assert res.warnings
    assert "پروژه بزرگ" in res.warnings[0]


def test_replan_keeps_reported_blocks():
    tid = add_task("کار", due=datetime(2026, 10, 22, 18), minutes=120)
    with session() as s:
        s.add(PlannedBlock(task_id=tid, start_at=datetime(2026, 10, 19, 8), end_at=datetime(2026, 10, 19, 9), status="done"))
    with session() as s:
        planner.plan(s, days=3)
        planner.plan(s, days=3)
        blocks = s.query(PlannedBlock).all()
        assert any(b.status == "done" for b in blocks)
        planned = [b for b in blocks if b.status == "planned"]
        assert sum((b.end_at - b.start_at).seconds // 60 for b in planned) == 120


def test_remaining_minutes_uses_logs_and_progress():
    tid = add_task("مقاله", minutes=120)
    with session() as s:
        s.add(WorkLog(task_id=tid, start_at=datetime(2026, 10, 19, 8), end_at=datetime(2026, 10, 19, 9), minutes=60))
    with session() as s:
        t = s.get(Task, tid)
        assert planner.remaining_minutes(s, t) == 60
        t.progress = 75
        assert planner.remaining_minutes(s, t) == 30


def test_availability_ok_when_light():
    add_task("کار سبک", due=datetime(2026, 10, 25, 18), minutes=60)
    with session() as s:
        res = planner.check_availability(s, datetime(2026, 10, 20, 17), datetime(2026, 10, 20, 20), 30)
    assert res["verdict"] == "ok"
    # the day still has more free time than the daily focus cap, so no usable capacity is lost
    assert res["productive_minutes_lost"] == 0


def test_availability_conflict_with_class():
    with session() as s:
        s.add(RecurringEvent(title="زبان", kind="language_class", weekdays=["tue"], start_time="18:00", end_time="20:00"))
    with session() as s:
        res = planner.check_availability(s, datetime(2026, 10, 20, 19), datetime(2026, 10, 20, 22), 0)
    assert res["verdict"] == "conflict"
    assert res["hard_conflicts"][0]["title"] == "زبان"


def test_availability_not_feasible_due_to_workload():
    # free at that hour, but way too much work before tomorrow's deadline
    prefs.set("max_focus_minutes_per_day", 720)
    add_task("پروژه", due=datetime(2026, 10, 20, 12), minutes=13 * 60)
    with session() as s:
        res = planner.check_availability(s, datetime(2026, 10, 19, 17), datetime(2026, 10, 19, 22), 0)
    assert res["verdict"] in ("not_feasible", "already_behind")

    # a smaller load that fits only without the outing -> not_feasible
    with session() as s:
        s.query(Task).delete()
    add_task("پروژه", due=datetime(2026, 10, 20, 9), minutes=9 * 60)
    with session() as s:
        res = planner.check_availability(s, datetime(2026, 10, 19, 15), datetime(2026, 10, 19, 21), 0)
    assert res["verdict"] == "not_feasible"
    assert res["tasks_newly_at_risk"][0]["title"] == "پروژه"


def test_events_block_free_time():
    with session() as s:
        s.add(Event(title="دندون‌پزشکی", start_at=datetime(2026, 10, 19, 10), end_at=datetime(2026, 10, 19, 22)))
    add_task("کار", due=datetime(2026, 10, 19, 21), minutes=60)
    with session() as s:
        res = planner.plan(s, days=1)
    assert res.warnings  # whole day is taken by the event
    assert all(not b["start"].startswith("2026-10-19") or b["start"] >= "2026-10-19 22" for b in res.blocks)


def test_daily_cap_respected():
    prefs.set("max_focus_minutes_per_day", 120)
    add_task("الف", minutes=600)
    with session() as s:
        res = planner.plan(s, days=2)
    per_day: dict[str, int] = {}
    for b in res.blocks:
        per_day[b["start"][:10]] = per_day.get(b["start"][:10], 0) + b["minutes"]
    assert per_day and all(v <= 120 for v in per_day.values())
