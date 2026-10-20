import asyncio
from datetime import datetime

from conftest import call

from app import followups, prefs
from app.agent import core, proactive
from app.db import Followup, Message, PlannedBlock, RecurringEvent, Task, WorkLog, session
from app.llm import ChatResult, LLMError


def run(coro):
    return asyncio.run(coro)


def _language_class():
    with session() as s:
        r = RecurringEvent(title="کلاس زبان", kind="language_class", weekdays=["tue", "thu"],
                           start_time="18:00", end_time="20:00", followup=True)
        s.add(r)
        s.flush()
        return r.id


def test_post_class_followup_generated_and_sent(clock, fake_llm):
    rid = _language_class()
    clock.value = datetime(2026, 10, 20, 19, 0)  # during Tuesday's class
    with session() as s:
        followups.generate(s)
        f = s.query(Followup).filter(Followup.kind == "post_class").one()
        assert f.due_at == datetime(2026, 10, 20, 20, 15)
        assert f.recurring_event_id == rid
        assert followups.due_now(s) == []

    clock.value = datetime(2026, 10, 20, 20, 16)
    fake_llm.script = [ChatResult(content="کلاس زبان چطور بود؟ تکلیف چی دادن؟")]
    sent = run(proactive.tick())
    assert sent >= 1
    with session() as s:
        msg = s.query(Message).filter(Message.kind == "post_class").one()
        assert "تکلیف" in msg.content
        assert msg.meta["quick_replies"]
        assert s.query(Followup).filter(Followup.kind == "post_class").one().status == "sent"
    # idempotent: no duplicate on the next tick
    run(proactive.tick())
    with session() as s:
        assert s.query(Message).filter(Message.kind == "post_class").count() == 1


def test_homework_flow_through_agent(clock, fake_llm):
    rid = _language_class()
    clock.value = datetime(2026, 10, 20, 20, 30)
    fake_llm.script = [
        call("add_task", title="Writing Task 2 درباره محیط زیست", due_next_session_of=rid, estimated_minutes=60),
        ChatResult(content="✅ ثبت شد: Writing Task 2 — تا پنجشنبه ۱۸:۰۰"),
    ]
    user, reply = run(core.handle_user_message("یه رایتینگ تسک ۲ داد درباره محیط زیست", "telegram"))
    assert reply["content"].startswith("✅")
    assert reply["meta"]["actions"][0]["tool"] == "add_task"
    with session() as s:
        t = s.query(Task).one()
        assert t.due_at == datetime(2026, 10, 22, 18, 0)
        assert t.category == "language"
        kinds = {f.kind for f in s.query(Followup).filter(Followup.task_id == t.id)}
        assert {"deadline", "overdue"} <= kinds
    # the system prompt carried the class & the tool definitions
    first = fake_llm.calls[0]
    assert "کلاس زبان" in first["messages"][0]["content"]
    assert any(tl["function"]["name"] == "add_task" for tl in first["tools"])
    # second LLM call saw the tool result
    assert fake_llm.calls[1]["messages"][-1]["role"] == "tool"


def test_history_keeps_action_ids(clock, fake_llm):
    fake_llm.script = [call("add_task", title="خرید کتاب"), ChatResult(content="ثبت شد")]
    run(core.handle_user_message("باید کتاب بخرم", "web"))
    fake_llm.script = [ChatResult(content="باشه")]
    run(core.handle_user_message("اونو پاک کن", "web"))
    hist = fake_llm.calls[-1]["messages"]
    assistant_turns = [m for m in hist if m["role"] == "assistant"]
    assert "add_task(task#1)" in assistant_turns[-1]["content"]


def test_llm_failure_gives_friendly_reply(fake_llm):
    async def boom(*a, **k):
        raise LLMError("timeout")

    fake_llm.chat = boom
    _, reply = run(core.handle_user_message("سلام", "web"))
    assert "دسترسی ندارم" in reply["content"]


def test_compose_fallback_when_llm_down(clock, fake_llm):
    _language_class()
    clock.value = datetime(2026, 10, 20, 19, 0)
    with session() as s:
        followups.generate(s)

    async def boom(*a, **k):
        raise LLMError("down")

    fake_llm.chat = boom
    clock.value = datetime(2026, 10, 20, 20, 20)
    run(proactive.tick())
    with session() as s:
        msg = s.query(Message).filter(Message.kind == "post_class").one()
        assert "کلاس «کلاس زبان» تموم شد" in msg.content


def test_deadline_followups_cancelled_when_done(clock, fake_llm):
    with session() as s:
        t = Task(title="تحویل پروژه", due_at=datetime(2026, 10, 22, 12), estimated_minutes=60,
                 created_at=datetime(2026, 10, 18, 9))
        s.add(t)
        s.flush()
        followups.generate(s)
        reminders = s.query(Followup).filter(Followup.kind == "deadline").all()
        assert sorted(f.due_at for f in reminders) == [datetime(2026, 10, 21, 12), datetime(2026, 10, 22, 9)]
        t.status = "done"
    clock.value = datetime(2026, 10, 21, 12, 1)
    with session() as s:
        assert followups.due_now(s) == []
        assert all(f.status == "cancelled" for f in s.query(Followup).filter(Followup.kind == "deadline", Followup.due_at <= clock.value))


def test_deadline_change_invalidates_old_reminders(clock):
    with session() as s:
        t = Task(title="الف", due_at=datetime(2026, 10, 22, 12), created_at=datetime(2026, 10, 18, 9))
        s.add(t)
        s.flush()
        followups.generate(s)
        t.due_at = datetime(2026, 10, 25, 12)
        followups.generate(s)
    clock.value = datetime(2026, 10, 21, 12, 1)
    with session() as s:
        due = followups.due_now(s)
        assert due == []  # the reminder for the old deadline is dropped


def test_quiet_hours_postpone(clock):
    prefs.set("wake_time", "08:00")
    prefs.set("sleep_time", "23:30")
    with session() as s:
        s.add(Followup(kind="deadline", due_at=datetime(2026, 10, 20, 2, 0), payload={}))
    clock.value = datetime(2026, 10, 20, 2, 1)
    with session() as s:
        assert followups.due_now(s) == []
        f = s.query(Followup).one()
        assert f.due_at == datetime(2026, 10, 20, 8, 15)
        assert f.status == "pending"


def test_block_nudges_and_focus(clock, fake_llm):
    prefs.set("daily_brief_time", "23:00")
    with session() as s:
        t = Task(title="مطالعه فصل ۳", estimated_minutes=90)
        s.add(t)
        s.flush()
        b = PlannedBlock(task_id=t.id, start_at=datetime(2026, 10, 19, 11), end_at=datetime(2026, 10, 19, 12, 30))
        s.add(b)
        s.flush()
        followups.generate(s)
        kinds = sorted(f.kind for f in s.query(Followup).filter(Followup.block_id == b.id))
        assert kinds == ["block_end", "block_start"]
        tid, bid = t.id, b.id
    clock.value = datetime(2026, 10, 19, 11, 0)
    fake_llm.script = [ChatResult(content="وقت مطالعه‌ست! شروع کنیم؟")]
    run(proactive.tick())
    with session() as s:
        m = s.query(Message).filter(Message.kind == "block_start").one()
        assert "شروع کردم ▶️" in m.meta["quick_replies"]

    # user starts working via quick reply -> agent calls start_focus
    fake_llm.script = [call("start_focus", task_id=tid), ChatResult(content="تایمر روشن شد ⏱")]
    run(core.handle_user_message("شروع کردم ▶️", "telegram"))
    clock.value = datetime(2026, 10, 19, 12, 10)
    fake_llm.script = [call("stop_focus", progress=50), ChatResult(content="۷۰ دقیقه ثبت شد")]
    run(core.handle_user_message("تموم کردم", "telegram"))
    with session() as s:
        lg = s.query(WorkLog).one()
        assert lg.minutes == 70
        assert s.get(Task, tid).progress == 50
        assert s.get(PlannedBlock, bid).status == "done"
        # remaining work was re-planned automatically after the progress update
        assert s.query(PlannedBlock).filter(PlannedBlock.status == "planned").count() >= 1


def test_idle_checkin(clock, fake_llm):
    prefs.set("idle_checkin_hours", 2)
    with session() as s:
        s.add(Message(role="user", content="سلام", channel="web", created_at=datetime(2026, 10, 19, 7, 0)))
    clock.value = datetime(2026, 10, 19, 11, 0)
    with session() as s:
        followups.generate(s)
        assert s.query(Followup).filter(Followup.kind == "idle_checkin").count() == 1
        followups.generate(s)
        assert s.query(Followup).filter(Followup.kind == "idle_checkin").count() == 1


def test_daily_brief_runs_planner(clock, fake_llm):
    prefs.set("daily_brief_time", "09:50")
    with session() as s:
        s.add(Task(title="تمرین ریاضی", due_at=datetime(2026, 10, 20, 20), estimated_minutes=60))
    fake_llm.script = [ChatResult(content="☀️ صبح بخیر! امروز ...")]
    run(proactive.tick())
    with session() as s:
        assert s.query(Message).filter(Message.kind == "daily_brief").count() == 1
        assert s.query(PlannedBlock).count() >= 1


def test_weekly_report_fallback(clock, fake_llm):
    async def boom(*a, **k):
        raise LLMError("down")

    fake_llm.chat = boom
    with session() as s:
        t = Task(title="طراحی صفحه ورود", category="work", status="done", completed_at=datetime(2026, 10, 18, 15))
        s.add(t)
        s.flush()
        s.add(WorkLog(task_id=t.id, category="work", start_at=datetime(2026, 10, 18, 12),
                      end_at=datetime(2026, 10, 18, 15), minutes=180))
        s.add(Task(title="کتاب زبان", category="language", status="done", completed_at=datetime(2026, 10, 18, 16)))
    from app import reports

    rep = run(reports.generate(0))
    assert "طراحی صفحه ورود" in rep.work_report
    assert "کتاب زبان" not in rep.work_report  # personal stuff never goes to the employer
    assert "۳ ساعت" in rep.personal_report


def test_changing_brief_time_replaces_pending_brief(clock):
    prefs.set("daily_brief_time", "11:00")
    with session() as s:
        followups.generate(s)
    prefs.set("daily_brief_time", "12:00")
    with session() as s:
        followups.generate(s)
        briefs = s.query(Followup).filter(Followup.kind == "daily_brief", Followup.due_at < datetime(2026, 10, 20)).all()
        assert sorted(f.due_at.hour for f in briefs) == [11, 12]
    clock.value = datetime(2026, 10, 19, 12, 1)
    with session() as s:
        due = [f for f in followups.due_now(s) if f.kind == "daily_brief"]
        assert [f.due_at.hour for f in due] == [12]


def test_history_roles_alternate(fake_llm):
    with session() as s:
        s.add(Message(role="user", content="سلام", channel="web"))
        s.add(Message(role="assistant", content="یادآوری ۱", channel="system", kind="deadline"))
        s.add(Message(role="assistant", content="یادآوری ۲", channel="system", kind="idle_checkin"))
    hist = core._history_messages()
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert "یادآوری ۱" in hist[1]["content"] and "یادآوری ۲" in hist[1]["content"]
