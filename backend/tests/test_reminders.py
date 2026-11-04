"""Reminder-first behaviour, from the user's real chats: past quizzes are over, exact relative reminders, do-not-disturb,
skipped classes and one-tap answers under reminders."""

import asyncio
from datetime import datetime, timedelta

from app import actions, followups, outbox, planner, prefs
from app.agent import proactive, tools
from app.db import Followup, Message, PlannedBlock, RecurringEvent, Task, session


def run(c):
    return asyncio.run(c)


def ex(_tool, **args):
    ctx = tools.Ctx(channel="web", turn_id="t-test")
    res = run(tools.execute(_tool, args, ctx))
    tools.finalize(ctx)
    return res


def _pending(kind=None):
    with session() as s:
        q = s.query(Followup).filter(Followup.status == "pending")
        return [f for f in q.all() if kind is None or f.kind == kind]


# ---------------------------------------------------------------- the past is past
def test_quiz_prep_is_closed_once_the_quiz_is_over_and_never_planned(clock):
    """Seen live: «آمادگی کوییز مدار» kept being planned and nagged about for days after the quiz."""
    with session() as s:
        s.add(Task(title="آمادگی کوییز مدار", category="university", estimated_minutes=120,
                   due_at=datetime(2026, 10, 19, 9, 0)))
        s.add(Task(title="تمرین اتوماتا", category="university", estimated_minutes=60, due_at=datetime(2026, 10, 18, 23, 59)))
        s.add(Task(title="مقاله RAG", category="work", estimated_minutes=60, due_at=datetime(2026, 10, 22, 18, 0)))
    with session() as s:
        followups.generate(s)
    with session() as s:
        quiz = s.query(Task).filter(Task.title.contains("کوییز")).one()
        late = s.query(Task).filter(Task.title.contains("اتوماتا")).one()
        assert quiz.status == "cancelled" and planner.EXPIRED_NOTE in quiz.notes
        assert late.status == "todo"  # a late homework is asked about, not thrown away
        planned = {b.task_id for b in s.query(PlannedBlock).all()}
        assert quiz.id not in planned and late.id not in planned and planned  # only the RAG paper gets time
        assert not planner.feasibility(s).at_risk
        assert [t.title for t in planner.recently_expired(s)] == ["آمادگی کوییز مدار"]


def test_overdue_task_is_still_planned_while_the_user_is_on_it(clock):
    with session() as s:
        t = Task(title="گزارش اسپرینت", status="doing", estimated_minutes=60, due_at=datetime(2026, 10, 19, 9, 0))
        s.add(t)
        s.flush()
        assert t in planner.schedulable_tasks(s)
        t.status = "todo"
        assert t not in planner.schedulable_tasks(s)


def test_expires_flag_overrides_the_title_guess(clock):
    with session() as s:
        a = Task(title="ارائه پروژه", expires=False, due_at=datetime(2026, 10, 19, 9, 0))
        b = Task(title="رزرو سالن", expires=True, due_at=datetime(2026, 10, 19, 9, 0))
        s.add_all([a, b])
        s.flush()
        planner.retire_expired(s)
        assert a.status == "todo" and b.status == "cancelled"


# ---------------------------------------------------------------- reminder style (default)
def test_reminder_style_sends_reminders_not_work_session_pings(clock):
    prefs.set("idle_checkin_hours", 1)
    with session() as s:
        s.add(Task(title="پروژه شبکه", priority=1, estimated_minutes=600, due_at=datetime(2026, 10, 21, 18, 0)))
        s.add(Followup(kind="block_start", due_at=datetime(2026, 10, 19, 9, 55), dedupe_key="old:block"))
    with session() as s:
        planner.plan(s)
        followups.generate(s)
    kinds = {f.kind for f in _pending()}
    assert "deadline" in kinds and "overdue" in kinds and "daily_brief" in kinds
    assert not kinds & followups.COACH_ONLY - {"block_start"}
    with session() as s:
        due = [f.kind for f in followups.due_now(s)]
    assert "block_start" not in due  # one left over from before is dropped, not sent
    prefs.set("followup_style", "coach")
    with session() as s:
        followups.generate(s)
    assert {"block_start", "daily_review"} <= {f.kind for f in _pending()}


# ---------------------------------------------------------------- do not disturb
def test_do_not_disturb_holds_follow_ups_but_lets_the_users_own_reminders_through(clock):
    with session() as s:
        t = Task(title="تمرین اتوماتا", estimated_minutes=90, due_at=datetime(2026, 10, 19, 23, 59))
        s.add(t)
        s.flush()
        s.add(Followup(kind="deadline", due_at=datetime(2026, 10, 19, 10, 40), task_id=t.id, dedupe_key="d1"))
        s.add(Followup(kind="focus_long", due_at=datetime(2026, 10, 19, 10, 50), dedupe_key="f1"))
        s.add(Followup(kind="custom", due_at=datetime(2026, 10, 19, 11, 0), note="زنگ بزنی به رضا", created_by="agent"))
    clock.value = datetime(2026, 10, 19, 12, 31)
    r = ex("do_not_disturb", until="2026-10-19 01:30")  # «تا ۱:۳۰» said at lunch time = 13:30
    assert r["quiet_until"] == "2026-10-19 13:30", r
    with session() as s:
        assert [f.kind for f in followups.due_now(s)] == ["custom"]
    with session() as s:
        kinds = {f.kind: f for f in s.query(Followup).all()}
        assert kinds["focus_long"].status == "cancelled"
        assert kinds["deadline"].status == "pending" and kinds["deadline"].due_at == datetime(2026, 10, 19, 13, 32)
    clock.value = datetime(2026, 10, 19, 13, 40)
    with session() as s:
        assert "deadline" in [f.kind for f in followups.due_now(s)]


def test_do_not_disturb_default_and_off(clock):
    assert ex("do_not_disturb")["quiet_until"] == "2026-10-19 12:00"
    with session() as s:
        assert followups.quiet_until(s, clock.value) == datetime(2026, 10, 19, 12, 0)
    ex("do_not_disturb", off=True)
    with session() as s:
        assert followups.quiet_until(s, clock.value) is None


# ---------------------------------------------------------------- reminders
def test_relative_reminder_is_computed_by_the_server(clock):
    """Seen live: «نیم ساعت دیگه یادم بنداز» at 16:00 became 16:45."""
    r = ex("schedule_followup", in_minutes=30, note="بشینی پای منوی شام کد و کدی")
    assert "error" not in r, r
    with session() as s:
        f = s.query(Followup).one()
        assert f.due_at == datetime(2026, 10, 19, 10, 30) and f.kind == "custom"
    assert "error" in ex("schedule_followup", at="2026-10-19 09:00", note="دیر")
    assert "error" in ex("schedule_followup", note="بی‌زمان")


def test_custom_reminder_is_sent_word_for_word_with_buttons(clock, fake_llm):
    prefs.set("daily_brief_time", "23:00")
    ex("schedule_followup", in_minutes=60, note="زنگ بزنی به رضا")
    clock.value = datetime(2026, 10, 19, 11, 0)
    run(proactive.tick())
    assert not fake_llm.calls  # no model call for a reminder
    with session() as s:
        m = s.query(Message).filter(Message.kind == "custom").one()
        mid, meta = m.id, dict(m.meta)
        assert m.content == "⏰ یادآوری: زنگ بزنی به رضا"
    assert [a["id"] for a in meta["reminder_actions"]] == ["done", "snooze10", "snooze60"]
    rows = outbox.buttons_for(mid, meta)
    assert any(b["data"] == f"ra:{mid}:snooze10" for row in rows for b in row)
    assert not any(b["data"].startswith("sz:") for row in rows for b in row)  # snooze is one of the buttons now
    text = actions.reminder_action(mid, "snooze10")
    assert "۱۱:۱۰" in text
    assert [f.due_at for f in _pending("custom")] == [datetime(2026, 10, 19, 11, 10)]
    assert actions.reminder_action(mid, "done") == text  # answered once; the second tap changes nothing


def test_overdue_question_has_buttons_that_act_at_once(clock, fake_llm):
    prefs.set("daily_brief_time", "23:00")
    with session() as s:
        a = Task(title="تمرین سری ۳", estimated_minutes=60, due_at=datetime(2026, 10, 18, 20, 0))
        b = Task(title="خلاصه فصل ۴", estimated_minutes=60, due_at=datetime(2026, 10, 18, 21, 0))
        s.add_all([a, b])
    run(proactive.tick())  # generates the overdue questions (deadline + 30 min, still fresh) and sends them
    assert not fake_llm.calls
    with session() as s:
        msgs = s.query(Message).filter(Message.kind == "overdue").order_by(Message.id).all()
        assert len(msgs) == 2  # not merged into one model-written message: each has its own buttons
        ids = [m.id for m in msgs]
        assert [x["id"] for x in msgs[0].meta["reminder_actions"]] == ["done", "tomorrow", "drop"]
    assert "«تمرین سری ۳»" in actions.reminder_action(ids[0], "tomorrow")
    actions.reminder_action(ids[1], "drop")
    with session() as s:
        a, b = s.query(Task).order_by(Task.id).all()
        assert a.due_at == datetime(2026, 10, 20, 20, 0) and a.status == "todo"
        assert b.status == "cancelled"


# ---------------------------------------------------------------- skipped class
def test_skip_class_cancels_the_after_class_question(clock):
    with session() as s:
        s.add(RecurringEvent(title="مفاهیم پیشرفته کامپیوتر", kind="university_class", weekdays=["mon"],
                             start_time="16:30", end_time="18:00", followup=True))
        s.add(RecurringEvent(title="سیستم‌های بی‌درنگ", kind="university_class", weekdays=["mon"],
                             start_time="08:00", end_time="09:30", followup=True))
    r = ex("skip_class")  # nothing generated yet; the class still ahead today is the one
    assert r["skipped"]["title"] == "مفاهیم پیشرفته کامپیوتر", r
    with session() as s:
        followups.generate(s)
    skipped = r["skipped"]["class_id"]
    assert not [f for f in _pending("post_class") if f.recurring_event_id == skipped]
    clock.value = datetime(2026, 10, 19, 18, 30)
    with session() as s:
        assert skipped not in [f.recurring_event_id for f in followups.due_now(s)]


def test_skip_class_by_name_and_unknown_day(clock):
    with session() as s:
        s.add(RecurringEvent(title="نظریه زبان‌ها و ماشین‌ها", kind="university_class", weekdays=["tue"],
                             start_time="16:30", end_time="18:00", followup=True))
    assert ex("skip_class", **{"class": "اتوماتا"}).get("error")  # not that day
    r = ex("skip_class", **{"class": "نظریه زبان", "date": "2026-10-20"})
    assert r["skipped"]["start"] == "2026-10-20 16:30", r
