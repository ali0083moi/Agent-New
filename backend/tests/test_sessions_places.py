"""Chat sessions, tool groups (token savings), places & travel, learned durations, batched reminders."""

import asyncio
from datetime import datetime, timedelta

from conftest import call, sid
from fastapi.testclient import TestClient

from app import followups, insights, notify, places, planner, prefs, sessions, telegram
from app.agent import core, proactive, toolsets, tools
from app.db import ChatSession, Event, Followup, Message, Place, RecurringEvent, Summary, Task, WorkLog, session
from app.llm import ChatResult


def run(c):
    return asyncio.run(c)


def ex(_tool, **args):
    ctx = tools.Ctx(channel="web", turn_id="t-test")
    res = run(tools.execute(_tool, args, ctx))
    tools.finalize(ctx)
    return res


# ---------------------------------------------------------------- sessions
def test_messages_go_to_the_current_session_and_roll_over_after_idle(clock):
    a = notify.store_message("user", "سلام، امروز چیکار کنم؟", "web")
    notify.store_message("assistant", "اول تکلیف ریاضی", "web")
    with session() as s:
        cs = s.get(ChatSession, a["session_id"])
        assert cs.title == "سلام، امروز چیکار کنم؟" and cs.last_message_at is not None
    clock.value += timedelta(hours=7)  # longer than session_idle_hours (6)
    b = notify.store_message("assistant", "صبح بخیر! برنامه امروز…", "system", kind="daily_brief")
    assert b["session_id"] != a["session_id"]
    with session() as s:
        assert sessions.current_id(s) == b["session_id"]
    # history only contains the new session
    assert [m["content"] for m in core._history_messages()] == []  # starts with a user turn; the brief alone is dropped
    notify.store_message("user", "باشه", "telegram")
    hist = core._history_messages()
    assert len(hist) == 1 and "برنامه امروز" not in hist[0]["content"]


def test_new_session_is_reused_while_empty_and_old_one_is_summarized(fake_llm):
    with session() as s:
        first = sessions.current_id(s)
    for i in range(3):
        notify.store_message("user", f"پیام {i}", "web")
        notify.store_message("assistant", f"جواب {i}", "web")
    with session() as s:
        cs, ended = sessions.new_session(s)
        assert ended == first and cs.id != first
        again, ended2 = sessions.new_session(s)  # still empty → no second empty session
        assert again.id == cs.id and ended2 is None
    fake_llm.script = [ChatResult(content="- کاربر سه سؤال پرسید و قرار شد فردا پیگیری کنم")]
    from app import memory

    assert run(memory.summarize_session(first)) is True
    with session() as s:
        assert "پیگیری" in sessions.previous_summary(s, cs.id)


def test_agent_sees_previous_session_summary_but_not_its_messages(fake_llm):
    notify.store_message("user", "رمز وای‌فای خونه ۱۲۳۴۵ هست", "web")
    notify.store_message("assistant", "باشه", "web")
    with session() as s:
        old = sessions.current_id(s)
        s.get(ChatSession, old).summary = "- قرار شد فردا درباره پروژه ماشین بپرسم"
        sessions.new_session(s)
    fake_llm.script = [ChatResult(content="سلام!")]
    run(core.handle_user_message("سلام", "web"))
    sent = fake_llm.calls[0]["messages"]
    text = "\n".join(m["content"] for m in sent if isinstance(m.get("content"), str))
    assert "قرار شد فردا درباره پروژه ماشین بپرسم" in text
    assert "۱۲۳۴۵" not in text


def test_summaries_are_per_session(fake_llm):
    with session() as s:
        other = ChatSession(title="قدیمی")
        s.add(other)
        s.flush()
        s.add(Summary(session_id=other.id, start_message_id=1, end_message_id=2, content="خلاصه گفتگوی دیگر"))
    from app import memory

    assert memory.latest_summaries(2) == []


def test_adopt_legacy_messages():
    with session() as s:
        s.add(Message(role="user", content="پیام قدیمی", channel="web", created_at=datetime(2026, 10, 1)))
        s.add(Message(role="assistant", content="جواب قدیمی", channel="web", created_at=datetime(2026, 10, 1, 0, 1)))
    with session() as s:
        sessions.adopt_legacy(s)
        legacy = s.query(ChatSession).filter(ChatSession.title == "گفتگوهای قبلی").one()
        assert s.query(Message).filter(Message.session_id == legacy.id).count() == 2
        sessions.adopt_legacy(s)  # idempotent
        assert s.query(ChatSession).filter(ChatSession.title == "گفتگوهای قبلی").count() == 1


def test_sessions_api(fake_llm):
    from app.main import create_app

    client = TestClient(create_app())
    fake_llm.script = [ChatResult(content="جواب اول")]
    first = client.post("/api/chat", data={"text": "سلام"}).json()["user"]["session_id"]
    new = client.post("/api/sessions", json={}).json()
    assert new["id"] != first and new["current"]
    fake_llm.script = [ChatResult(content="جواب دوم")]
    client.post("/api/chat", data={"text": "گفتگوی دوم"})
    listing = client.get("/api/sessions").json()
    assert [x["id"] for x in listing["sessions"]][:2] == [new["id"], first] and listing["current"] == new["id"]
    assert [m["content"] for m in client.get(f"/api/messages?session_id={first}").json()["messages"]] == ["سلام", "جواب اول"]
    # writing in an old session from the web makes it current again
    fake_llm.script = [ChatResult(content="ادامه")]
    r = client.post("/api/chat", data={"text": "برگشتم", "session_id": str(first)}).json()
    assert r["assistant"]["session_id"] == first and client.get("/api/sessions").json()["current"] == first
    assert client.patch(f"/api/sessions/{first}", json={"title": "شروع"}).json()["title"] == "شروع"
    assert client.delete(f"/api/sessions/{new['id']}").json()["ok"]
    assert all(x["id"] != new["id"] for x in client.get("/api/sessions").json()["sessions"])


def test_telegram_new_command(monkeypatch):
    sent = []
    bot = telegram.TelegramBot("TOKEN", "https://api.telegram.org")

    async def fake_send(chat_id, text, **kw):
        sent.append(text)

    monkeypatch.setattr(bot, "send", fake_send)
    prefs.set("telegram_chat_id", 42)
    notify.store_message("user", "سلام", "telegram")
    with session() as s:
        before = sessions.current_id(s)
    run(bot.handle({"update_id": 1, "message": {"chat": {"id": 42}, "from": {"id": 42}, "text": "/new"}}))
    with session() as s:
        assert sessions.current_id(s) != before
    assert "گفتگوی جدید" in sent[0]


# ---------------------------------------------------------------- tool groups
def test_every_tool_belongs_to_core_or_a_group():
    allowed = set(toolsets.tool_names(set(toolsets.GROUPS)))
    assert allowed == set(tools.REGISTRY)


def test_group_selection():
    assert toolsets.select(["فردا ساعت ۵ جلسه دارم"]) == ["schedule"]
    assert toolsets.select(["این رایتینگ تسک ۲ رو تصحیح کن"]) == ["ielts"]
    assert "apply" in toolsets.select(["به استاد اسمیت ایمیل زدم"])
    assert toolsets.select(["پروژه ماشین رو جلو بردم"]) == ["projects"]
    assert "places" in toolsets.select(["از خونه تا شرکت ۴۰ دقیقه طول می‌کشه"])
    assert toolsets.select(["سلام خوبی؟"]) == []
    assert toolsets.select(["باشه"], kinds=["habit_reminder"]) == ["habits"]
    assert set(toolsets.select(["ببین"], attachments=["image"])) == {"schedule", "ielts"}
    assert toolsets.select(["ok"], sticky=["planning", "nonsense"]) == ["planning"]


def test_agent_sends_core_tools_and_can_load_more(fake_llm):
    fake_llm.script = [call("load_tools", groups=["habits"]), call("add_habit", title="ورزش", target=1),
                       ChatResult(content="✅ عادت ورزش ثبت شد")]
    _, reply = run(core.handle_user_message("سلام خوبی؟ یه چیزی می‌خواستم", "web"))
    first, second = fake_llm.calls[0]["tools"], fake_llm.calls[1]["tools"]
    names1 = {t["function"]["name"] for t in first}
    names2 = {t["function"]["name"] for t in second}
    assert names1 == set(toolsets.CORE) and "add_habit" not in names1
    assert "add_habit" in names2
    assert reply["meta"]["groups"] == ["habits"]
    # the next turn keeps the group (sticky) without calling load_tools again
    fake_llm.script = [ChatResult(content="باشه")]
    run(core.handle_user_message("مرسی", "web"))
    assert "add_habit" in {t["function"]["name"] for t in fake_llm.calls[-1]["tools"]}


def test_history_shortens_old_messages():
    long = "خیلی طولانی " * 200
    for i in range(10):
        notify.store_message("user", f"سؤال {i}", "web")
        notify.store_message("assistant", long, "web")
    hist = core._history_messages()
    assert hist[1]["role"] == "assistant" and len(hist[1]["content"]) < 600 and "[shortened]" in hist[1]["content"]
    assert hist[-1]["content"].startswith(long.strip()[:50])


# ---------------------------------------------------------------- places & travel
def _setup_places():
    ex("set_place", name="خانه", kind="home", aliases=["خونه"])
    ex("set_place", name="شرکت", kind="work", aliases=["دفتر"])
    ex("set_place", name="دانشگاه شریف", kind="university", aliases=["دانشگاه"])
    ex("set_travel_time", from_place="خونه", to_place="شرکت", minutes=40, mode="metro")
    ex("set_travel_time", from_place="خونه", to_place="دانشگاه", minutes=60, mode="metro")
    ex("set_travel_time", from_place="دفتر", to_place="دانشگاه شریف", minutes=30, mode="bus")
    prefs.set("prep_minutes", 30)


def test_place_resolution_and_observed_travel_time():
    _setup_places()
    with session() as s:
        assert places.resolve(s, "دانشگاه شریف، کلاس ۲۰۳").name == "دانشگاه شریف"
        assert places.resolve(s, "دفتر").name == "شرکت"
        assert places.resolve(s, "سینما") is None
    r = ex("set_travel_time", from_place="خانه", to_place="شرکت", minutes=60, observed=True)
    assert r["travel"]["usual_minutes"] == 50 and r["travel"]["logged_trip"]  # metro: averaged into the usual time
    r = ex("set_travel_time", from_place="خانه", to_place="سینما", minutes=10)
    assert "unknown place" in r["error"]


def test_busy_intervals_include_prep_and_travel(clock):
    _setup_places()
    day = datetime(2026, 10, 20)  # Tuesday
    with session() as s:
        s.add(Event(title="جلسه شرکت", kind="meeting", start_at=day.replace(hour=10), end_at=day.replace(hour=12),
                    location="دفتر"))
        s.add(Event(title="سمینار", kind="other", start_at=day.replace(hour=13), end_at=day.replace(hour=15),
                    location="دانشگاه"))
    from app import cal

    with session() as s:
        busy = {t: (a, b) for a, b, t in cal.busy_intervals(s, day, day + timedelta(days=1))}
        raw = {t: (a, b) for a, b, t in cal.busy_intervals(s, day, day + timedelta(days=1), class_buffer=0)}
    # from home: 30 min getting ready + 40 min trip; straight on to the university afterwards (no trip home in between)
    assert busy["جلسه شرکت"] == (day.replace(hour=8, minute=50), day.replace(hour=12))
    # company → university 30 min, then 60 min back home
    assert busy["سمینار"] == (day.replace(hour=12, minute=30), day.replace(hour=16))
    assert raw["سمینار"] == (day.replace(hour=13), day.replace(hour=15))


def test_leave_reminders_are_generated_and_sent_without_a_model_call(clock, fake_llm):
    _setup_places()
    with session() as s:
        s.add(RecurringEvent(title="ریاضی", kind="university_class", weekdays=["tue"], start_time="14:00",
                             end_time="16:00", location="دانشگاه شریف"))
    clock.value = datetime(2026, 10, 20, 9, 0)
    with session() as s:
        followups.generate(s)
        f = s.query(Followup).filter(Followup.kind == "leave").one()
        assert f.due_at == datetime(2026, 10, 20, 12, 30)  # 14:00 - 60 min trip - 30 min getting ready
        assert f.payload["leave_at"] == "2026-10-20 13:00" and f.payload["from_home"]
    clock.value = datetime(2026, 10, 20, 12, 31)
    run(proactive.tick())
    with session() as s:
        m = s.query(Message).filter(Message.kind == "leave").one()
    # the fixed text went out as is (a model call would have produced the fake model's default reply)
    assert m.content.startswith("🚪 وقت حاضر شدنه") and "۱۳:۰۰" in m.content and "دانشگاه شریف" in m.content


def test_check_availability_counts_the_trip(clock):
    _setup_places()
    r = ex("check_availability", start="2026-10-22 18:00", end="2026-10-22 20:00", place="دانشگاه")
    assert r["trip"]["before"] == 90 and r["window"]["start"] == "2026-10-22 16:30"
    assert r["window"]["end"] == "2026-10-22 21:00"


def test_profile_gaps_and_context(clock):
    with session() as s:
        gaps = places.profile_gaps(s)
    assert any("where they live" in g for g in gaps) and any("get ready" in g for g in gaps)
    _setup_places()
    prefs.set("user_name", "علی")
    with session() as s:
        gaps = places.profile_gaps(s)
        lines = "\n".join(places.context_lines(s))
    assert not any("get ready" in g for g in gaps)
    assert "خانه ↔ شرکت 40m (metro)" in lines and "getting ready before leaving home: 30 min" in lines


# ---------------------------------------------------------------- learned durations
def test_add_task_uses_how_long_similar_tasks_took(clock):
    with session() as s:
        for i, minutes in enumerate([100, 120, 140]):
            t = Task(title=f"تمرین برنامه‌سازی موبایل جلسه {i}", category="university", status="done",
                     estimated_minutes=60, completed_at=datetime(2026, 10, 10 + i, 20))
            s.add(t)
            s.flush()
            s.add(WorkLog(task_id=t.id, start_at=datetime(2026, 10, 10 + i, 18), end_at=datetime(2026, 10, 10 + i, 20),
                          minutes=minutes, category="university", productive=True))
    r = ex("add_task", title="تمرین برنامه‌سازی موبایل جلسه ۸", category="university")
    assert r["task"]["estimated_minutes"] == 120 and "from history" in r["duration_history"]
    r = ex("add_task", title="تمرین برنامه‌سازی موبایل جلسه ۹", category="university", estimated_minutes=30)
    assert "differs from history" in r["duration_history"]
    with session() as s:
        assert insights.typical_minutes(s)["university"] == {"minutes": 120, "samples": 3}


# ---------------------------------------------------------------- fewer, cheaper proactive messages
def test_rescue_at_most_once_a_day(clock):
    with session() as s:
        for i in range(3):
            s.add(Task(title=f"پروژه بزرگ {i}", estimated_minutes=900, due_at=clock.value + timedelta(hours=10 + i)))
    with session() as s:
        followups.generate(s)
    followups._last_rescue_check = None
    with session() as s:
        s.add(Task(title="یکی دیگه", estimated_minutes=900, due_at=clock.value + timedelta(hours=12)))
    with session() as s:
        followups.generate(s)
        assert s.query(Followup).filter(Followup.kind == "rescue").count() == 1


def test_simultaneous_task_reminders_become_one_message(clock, fake_llm):
    with session() as s:
        for i in range(3):
            t = Task(title=f"تکلیف {i}", due_at=clock.value + timedelta(hours=5), estimated_minutes=30)
            s.add(t)
            s.flush()
            s.add(Followup(kind="deadline", task_id=t.id, due_at=clock.value - timedelta(minutes=1),
                           payload={"due": (clock.value + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")}))
    fake_llm.script = [ChatResult(content="سه تا ددلاین نزدیک داری: …")]
    run(proactive.tick())
    triggers = [c["messages"][-1]["content"] for c in fake_llm.calls if c["purpose"] == "compose"]
    assert sum("Combine them into ONE" in x for x in triggers) == 1
    # no separate message per task (the daily brief / idle check-in of the same tick may still mention them)
    assert not [x for x in triggers if "Combine them" not in x and "Deadline" in x]
    with session() as s:
        assert s.query(Followup).filter(Followup.kind == "deadline", Followup.status == "sent").count() == 3
        assert s.query(Message).filter(Message.kind == "deadline").count() == 1


def test_bad_ids_inside_lists_are_dropped():
    """Seen in the model evals: every class of a timetable came with goal_id=0 → FOREIGN KEY failed."""
    r = ex("add_recurring_events", events=[
        {"title": "ریاضی ۲", "kind": "university_class", "weekdays": ["sat"], "start_time": "08:00", "end_time": "10:00",
         "goal_id": 0},
        {"title": "فیزیک", "kind": "university_class", "weekdays": ["sun"], "start_time": "10:00", "end_time": "12:00",
         "goal_id": "۷"},
    ])
    assert "error" not in r, r
    assert len(r["created"]) == 2
    assert any("events[0].goal_id=0" in w for w in r["warnings"])
    with session() as s:
        assert all(x.goal_id is None for x in s.query(RecurringEvent).all())


def test_recommended_models_applied_once_unless_user_chose():
    with session() as s:
        assert prefs.apply_model_defaults(s)["llm_chat_model"] == "gemini-3.1-flash-lite"
        prefs.set("llm_chat_model", "gpt-4.1", s=s)  # the user changes it back in Settings
        assert prefs.apply_model_defaults(s) == {}
        assert prefs.get("llm_chat_model", s=s) == "gpt-4.1"


def test_user_choice_is_kept():
    with session() as s:
        prefs.set("llm_chat_model", "claude-sonnet-5-5", s=s)
        assert prefs.apply_model_defaults(s) == {}
        assert prefs.get("llm_fast_model", s=s) == ""


def test_preferences_numbers_are_parsed():
    r = ex("update_preferences", changes={"prep_minutes": "۲۰ دقیقه", "session_idle_hours": "8", "daily_budget_usd": "0.5"})
    assert r["applied"] == {"prep_minutes": 20, "session_idle_hours": 8, "daily_budget_usd": 0.5}



# ---------------------------------------------------------------- traffic & routing
def test_car_trips_follow_traffic_by_hour(clock):
    from app import routing

    ex("set_place", name="خانه", kind="home")
    ex("set_place", name="شرکت", kind="work")
    ex("set_travel_time", from_place="خانه", to_place="شرکت", minutes=46, mode="taxi")  # usual (≈ rush hour) time
    with session() as s:
        a, b = places.resolve(s, "خانه"), places.resolve(s, "شرکت")
        rush = places.travel(s, a, b, datetime(2026, 10, 19, 8, 0))  # Monday 08:00
        night = places.travel(s, a, b, datetime(2026, 10, 19, 23, 0))
        friday = places.travel(s, a, b, datetime(2026, 10, 23, 8, 0))
    assert rush == round(46 / routing.TYPICAL_FACTOR * 2.4) and night < rush and friday < rush
    r = ex("travel_estimate", from_place="خانه", to_place="شرکت", arrive_by="2026-10-19 09:00")
    assert r["traffic_source"] == "default" and r["leave_at"] == "2026-10-19 08:12"


def test_reported_trips_teach_the_traffic_model(clock):
    ex("set_place", name="خانه", kind="home")
    ex("set_place", name="شرکت", kind="work")
    ex("set_travel_time", from_place="خانه", to_place="شرکت", minutes=46, mode="car")
    for day in (19, 20):  # two mornings it took 70 minutes
        ex("set_travel_time", from_place="خانه", to_place="شرکت", minutes=70, observed=True,
           when=f"2026-10-{day} 08:10")
    r = ex("travel_estimate", from_place="خانه", to_place="شرکت", at="2026-10-21 08:30")
    assert r["traffic_source"] == "learned" and r["minutes"] == 70
    r = ex("travel_estimate", from_place="خانه", to_place="شرکت", at="2026-10-21 17:00")
    assert r["traffic_source"] == "calibrated" and r["minutes"] > round(46 / 2.3 * 2.6)  # evening scaled up too
    with session() as s:
        assert places.resolve(s, "شرکت") and s.query(Place).count() == 2
        from app.db import TravelTime

        assert {x.minutes for x in s.query(TravelTime).all()} == {46}  # the usual time stays what the user said


def test_set_place_with_map_link_measures_routes(monkeypatch):
    from app import routing

    async def fake_route(a, b, mode=""):
        return {"minutes": 16.0, "km": 13.0}

    monkeypatch.setattr(routing, "route", fake_route)
    ex("set_place", name="خانه", kind="home", location="35.7036,51.3519")
    r = ex("set_place", name="شرکت", kind="work", location="https://www.google.com/maps/place/x/@35.7575,51.4215,17z")
    assert r["routes_measured"] == 1 and r["place"]["lat"] == 35.7575
    with session() as s:
        rows = places.travel_rows(s)
    assert rows[0]["estimated"] and rows[0]["minutes"] == round(16 * 2.3) and rows[0]["distance_km"] == 13.0
    # a user statement replaces the map estimate as the usual time
    ex("set_travel_time", from_place="خانه", to_place="شرکت", minutes=45, mode="taxi")
    with session() as s:
        assert places.travel_rows(s)[0]["minutes"] == 45 and not places.travel_rows(s)[0]["estimated"]


def test_coordinates_from_links_and_text():
    from app import routing

    assert routing.parse_coords("https://www.google.com/maps/@35.70,51.35,15z") == (35.70, 51.35)
    assert routing.parse_coords("https://maps.google.com/?q=35.7%2C51.4") == (35.7, 51.4)
    assert routing.parse_coords("https://balad.ir/location?lat=35.75&lng=51.42") == (35.75, 51.42)
    assert routing.parse_coords("۳۵.۷۰۳۶، ۵۱.۳۵۱۹") == (35.7036, 51.3519)
    assert routing.parse_coords("خیابان آزادی") is None


def test_telegram_location_pin_reaches_the_agent(monkeypatch, fake_llm):
    bot = telegram.TelegramBot("TOKEN", "https://api.telegram.org")
    seen = []

    async def fake_reply(chat_id, text, attachments, meta):
        seen.append(text)

    monkeypatch.setattr(bot, "reply", fake_reply)
    prefs.set("telegram_chat_id", 42)
    run(bot.handle({"message": {"chat": {"id": 42}, "caption": "اینجا شرکته",
                                "location": {"latitude": 35.7575, "longitude": 51.4215}}}))
    assert "اینجا شرکته" in seen[0] and "35.757500,51.421500" in seen[0]
