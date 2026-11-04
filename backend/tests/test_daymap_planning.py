"""The user's day as a person lives it: where they are, travel, free windows; deadline plans; human nudges."""

import asyncio
from datetime import datetime, timedelta

from app import daymap, followups, prefs
from app.agent import tools
from app.db import Event, Followup, PlannedBlock, RecurringEvent, Task, session


def run(c):
    return asyncio.run(c)


def ex(_tool, **args):
    ctx = tools.Ctx(channel="web", turn_id="t-test")
    res = run(tools.execute(_tool, args, ctx))
    tools.finalize(ctx)
    return res


def _setup_places():
    ex("set_place", name="خانه", kind="home", aliases=["خونه"])
    ex("set_place", name="شرکت", kind="work", aliases=["دفتر"])
    ex("set_place", name="دانشگاه شریف", kind="university", aliases=["دانشگاه"])
    ex("set_travel_time", from_place="خونه", to_place="شرکت", minutes=40, mode="metro")
    ex("set_travel_time", from_place="خونه", to_place="دانشگاه", minutes=60, mode="metro")
    ex("set_travel_time", from_place="دفتر", to_place="دانشگاه شریف", minutes=30, mode="bus")
    prefs.set("prep_minutes", 30)


DAY = datetime(2026, 10, 20)  # Tuesday


def _tuesday():
    with session() as s:
        s.add(RecurringEvent(title="کار شرکت", kind="work", weekdays=["tue"], start_time="09:00", end_time="15:00",
                             location="شرکت"))
        s.add(RecurringEvent(title="هوش مصنوعی", kind="university_class", weekdays=["tue"], start_time="16:00",
                             end_time="18:00", location="دانشگاه"))


def test_day_map_follows_the_user_between_places(clock):
    _setup_places()
    _tuesday()
    with session() as s:
        segs = daymap.day_map(s, DAY.date())
    lines = [x.line() for x in segs]
    kinds = [(x.kind, x.place) for x in segs]
    assert ("prep", "خانه") in kinds
    assert ("travel", "خانه→شرکت") in kinds and ("commitment", "شرکت") in kinds
    # straight from the company to the university (30 min by bus), then home (60 min)
    assert ("travel", "شرکت→دانشگاه شریف") in kinds and ("travel", "دانشگاه شریف→خانه") in kinds
    travel = next(x for x in segs if x.place == "شرکت→دانشگاه شریف")
    assert travel.start == DAY.replace(hour=15, minute=30) and travel.end == DAY.replace(hour=16)
    # the 30 minutes between work and leaving are spent at the company; the evening at home
    gap = next(x for x in segs if x.kind == "free" and x.start == DAY.replace(hour=15))
    assert gap.place == "شرکت"
    evening = next(x for x in segs if x.kind == "free" and x.start == DAY.replace(hour=19))
    assert evening.place == "خانه"
    assert any("on the way" in l for l in lines)


def test_where_now_and_busy_now(clock):
    _setup_places()
    _tuesday()
    with session() as s:
        w = daymap.where_now(s, DAY.replace(hour=10))
        assert w["kind"] == "commitment" and w["place"] == "شرکت" and "on the way" in w["next"]
        assert daymap.busy_now(s, DAY.replace(hour=16, minute=30)) == DAY.replace(hour=18)
        assert daymap.busy_now(s, DAY.replace(hour=20)) is None
        assert daymap.busy_now(s, DAY.replace(hour=15, minute=45)) is None  # on the bus: can read a message


def test_context_has_the_day_map(clock):
    from app.agent import prompt

    _setup_places()
    with session() as s:
        ctx = prompt.build_context(s)
    assert "The user's day" in ctx and "now:" in ctx


# ---------------------------------------------------------------- deadline plans
def test_add_task_returns_a_deadline_plan_with_places(clock):
    _setup_places()
    r = ex("add_task", title="گزارش آزمایشگاه", due="2026-10-21 18:00", estimated_minutes=180)
    plan = r["plan"]
    assert plan["fits"] and plan["needs_minutes"] == 180 and plan["planned_minutes"] >= 180
    assert plan["sessions"] and "@خانه" in plan["sessions"][0]
    # too much work for the time left → a clear warning
    r = ex("add_task", title="پایان‌نامه فصل ۱", due="2026-10-19 20:00", estimated_minutes=900)
    assert not r["plan"]["fits"] and "NOT fit" in r["plan"]["warning"]


def test_far_deadline_is_planned_beyond_a_week(clock):
    r = ex("add_task", title="مقاله کنفرانس", due="2026-11-05 12:00", estimated_minutes=600)
    assert r["plan"]["fits"]
    with session() as s:
        assert s.query(PlannedBlock).filter(PlannedBlock.task_id == r["task"]["id"]).count() >= 4


def test_study_hours_steer_deep_work(clock):
    prefs.set("study_hours", "20:00-23:00")
    r = ex("add_task", title="مطالعه عمیق الگوریتم", due="2026-10-23 23:00", estimated_minutes=120, energy="high")
    with session() as s:
        starts = [b.start_at.hour for b in s.query(PlannedBlock).filter(PlannedBlock.task_id == r["task"]["id"])]
    assert starts and all(20 <= h < 23 for h in starts)
    r = ex("update_preferences", changes={"study_hours": "۸ تا ۱۰، 20-23"})
    assert r["applied"]["study_hours"] == "08:00-10:00,20:00-23:00"


def test_learned_session_length(clock):
    from app import insights
    from app.db import WorkLog

    with session() as s:
        for i in range(8):
            st = datetime(2026, 10, 10 + i, 20)
            s.add(WorkLog(start_at=st, end_at=st + timedelta(minutes=50), minutes=50, category="university", productive=True))
        prof = insights.study_profile(s)
        assert prof["learned"] and prof["session_minutes"] == 50
        assert insights.session_minutes(prof, "university", 90) == 50
    r = ex("add_task", title="تمرین آمار", category="university", due="2026-10-22 18:00", estimated_minutes=150)
    with session() as s:
        lens = {int((b.end_at - b.start_at).total_seconds() // 60) for b in
                s.query(PlannedBlock).filter(PlannedBlock.task_id == r["task"]["id"])}
    assert max(lens) in (45, 50)  # the learned length (on the 15-minute grid), not the default 90


# ---------------------------------------------------------------- human nudges
def test_one_nudge_per_work_session_and_no_end_pings(clock):
    prefs.set("followup_style", "coach")
    with session() as s:
        t1 = Task(title="پروژه ماشین", estimated_minutes=200)
        t2 = Task(title="خواندن مقاله", estimated_minutes=60)
        s.add_all([t1, t2])
        s.flush()
        s.add_all([
            PlannedBlock(task_id=t1.id, start_at=DAY.replace(hour=11), end_at=DAY.replace(hour=12, minute=30)),
            PlannedBlock(task_id=t2.id, start_at=DAY.replace(hour=12, minute=45), end_at=DAY.replace(hour=13, minute=30)),
            PlannedBlock(task_id=t2.id, start_at=DAY.replace(hour=17), end_at=DAY.replace(hour=17, minute=30)),  # short
        ])
        s.flush()
        clock.value = DAY.replace(hour=9)
        followups.generate(s)
        nudges = s.query(Followup).filter(Followup.kind.in_(["block_start", "block_end"])).all()
        assert [(f.kind, f.due_at.hour) for f in nudges] == [("block_start", 11)]
        assert len(nudges[0].payload["blocks"]) == 2


def test_reminders_wait_until_class_is_over(clock):
    _setup_places()
    _tuesday()
    with session() as s:
        t = Task(title="ارسال فرم", due_at=DAY.replace(hour=23), status="todo")
        s.add(t)
        s.flush()
        f = Followup(kind="deadline", due_at=DAY.replace(hour=16, minute=30), task_id=t.id,
                     payload={"due": "2026-10-20 23:00", "hours_before": 6}, dedupe_key="x")
        s.add(f)
        s.flush()
        assert followups.due_now(s, DAY.replace(hour=16, minute=31)) == []
        assert f.due_at == DAY.replace(hour=18, minute=5) and f.status == "pending"
        assert [x.id for x in followups.due_now(s, DAY.replace(hour=18, minute=6))] == [f.id]
