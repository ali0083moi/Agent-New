"""Tests for the second wave of features (memory, undo, projects, habits, IELTS, adaptivity, delivery)."""

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta

import pytest
from conftest import call

from app import (actions, followups, gamify, habits, ielts, insights, memory, outbox, planner, prefs, push, rescue,
                 telegram)
from app.agent import core, proactive, tools
from app.db import (Commitment, Embedding, Followup, Habit, HabitLog, IeltsScore, Memory, Message, Outbox,
                    PlannedBlock, Summary, Task, VocabWord, WorkLog, session)
from app.llm import ChatResult


def run(c):
    return asyncio.run(c)


def ex(name, **args):
    ctx = tools.Ctx(channel="web", turn_id="t-test")
    res = run(tools.execute(name, args, ctx))
    tools.finalize(ctx)
    return res


# ---------------------------------------------------------------- memory
def _add_msgs(n, start_text="پیام"):
    with session() as s:
        for i in range(n):
            s.add(Message(role="user" if i % 2 == 0 else "assistant", content=f"{start_text} شماره {i}", channel="web",
                          created_at=datetime(2026, 10, 1, 10) + timedelta(minutes=i)))


def test_keyword_recall_finds_old_messages(fake_llm):
    with session() as s:
        s.add(Message(role="user", content="استاد پایگاه داده گفت پروژه باید با پستگرس باشه", channel="web",
                      created_at=datetime(2026, 9, 1)))
    _add_msgs(40)
    hits = run(memory.recall("پروژه پایگاه داده رو با چی بنویسم؟"))
    assert hits and "پستگرس" in hits[0]["text"]
    # messages inside the recent window are not "recalled"
    assert all("شماره 39" not in h["text"] for h in hits)


def test_semantic_recall_with_embeddings(fake_llm):
    def vec(text):
        # deterministic toy embedding: bag of "topics"
        topics = ["پایگاه", "آیلتس", "باشگاه"]
        return [1.0 if t in text else 0.0 for t in topics] + [0.01]

    async def embed(texts):
        return [vec(t) for t in texts]

    fake_llm.embed = embed
    memory.reset_cache()
    with session() as s:
        s.add(Message(role="user", content="من سه‌شنبه‌ها باشگاه می‌رم ساعت ۷ صبح", channel="web", created_at=datetime(2026, 9, 1)))
        s.add(Message(role="user", content="آیلتس رو برای اپلای کانادا می‌خوام", channel="web", created_at=datetime(2026, 9, 2)))
    _add_msgs(35)
    assert run(memory.index_pending(limit=200)) > 0
    hits = run(memory.recall("برنامه باشگاه من چیه؟"))
    assert hits[0]["text"].startswith("من سه‌شنبه‌ها باشگاه")


def test_summarize_old(fake_llm):
    _add_msgs(80)
    fake_llm.script = [ChatResult(content="- کاربر درباره پروژه حرف زد")]
    assert run(memory.summarize_old()) == 1
    with session() as s:
        sm = s.query(Summary).one()
        assert sm.start_message_id == 1 and sm.end_message_id == 40
    assert fake_llm.calls[-1]["purpose"] == "summary"


def test_extract_facts_adds_updates_and_dedupes(fake_llm):
    with session() as s:
        s.add(Memory(content="در شرکت آلفا کار می‌کند", category="work", source="agent"))
        u = Message(role="user", content="من دیگه تو آلفا نیستم، رفتم شرکت بتا. ضمناً صبح‌ها قهوه نمی‌خورم", channel="web")
        s.add(u)
        s.flush()
        uid = u.id
        s.add(Message(role="assistant", content="باشه", channel="web"))
    fake_llm.script = [ChatResult(content=json.dumps({
        "add": [{"content": "صبح‌ها قهوه نمی‌خورد", "category": "preference"}, {"content": "در شرکت بتا کار می‌کند"}],
        "update": [{"id": 1, "content": "در شرکت بتا کار می‌کند"}], "delete": []}, ensure_ascii=False))]
    out = run(memory.extract_facts(uid))
    assert out == {"added": 1, "updated": 1, "deleted": 0}
    with session() as s:
        contents = sorted(m.content for m in s.query(Memory))
        assert contents == ["در شرکت بتا کار می‌کند", "صبح‌ها قهوه نمی‌خورد"]


# ---------------------------------------------------------------- undo & confirmation
def test_undo_turn_reverts_creates_and_updates(fake_llm):
    fake_llm.script = [call("add_task", title="الف"), ChatResult(content="ثبت شد")]
    _, reply = run(core.handle_user_message("یه کار الف دارم", "web"))
    assert reply["meta"]["undoable"]
    with session() as s:
        assert s.query(Task).count() == 1
    actions.undo_message(reply["id"])
    with session() as s:
        assert s.query(Task).count() == 0
        assert s.get(Message, reply["id"]).meta["undone"] is True


def test_undo_restores_deleted_task_and_update():
    tid = ex("add_task", title="تحویل گزارش", estimated_minutes=60)["task"]["id"]
    ctx = tools.Ctx(turn_id="turn-upd")
    run(tools.execute("update_task", {"task_id": tid, "title": "عنوان جدید", "priority": 1}, ctx))
    from app import journal

    journal.undo("turn-upd")
    with session() as s:
        t = s.get(Task, tid)
        assert t.title == "تحویل گزارش" and t.priority == 2


def test_delete_needs_confirmation_in_a_later_turn():
    tid = ex("add_task", title="حذفی")["task"]["id"]
    ctx1 = tools.Ctx(turn_id="turn-1")
    r = run(tools.execute("delete_task", {"task_id": tid}, ctx1))
    assert r["needs_confirmation"]
    # confirming inside the same turn is not accepted
    r = run(tools.execute("delete_task", {"task_id": tid, "confirmed": True}, ctx1))
    assert r["needs_confirmation"]
    ctx2 = tools.Ctx(turn_id="turn-2")
    r = run(tools.execute("delete_task", {"task_id": tid, "confirmed": True}, ctx2))
    assert r == {"deleted": tid}
    from app import journal

    journal.undo("turn-2")
    with session() as s:
        assert s.get(Task, tid).title == "حذفی"


# ---------------------------------------------------------------- projects, exams, estimation, peaks
def test_subtasks_are_planned_and_complete_the_project():
    pid = ex("add_task", title="پروژه پایان‌ترم", due="2026-10-26 12:00", estimated_minutes=600)["task"]["id"]
    res = ex("break_down_task", task_id=pid, subtasks=[
        {"title": "طراحی", "estimated_minutes": 90, "due": "2026-10-21 12:00"},
        {"title": "پیاده‌سازی", "estimated_minutes": 180},
    ])
    ids = [t["id"] for t in res["subtasks"]]
    with session() as s:
        blocks = s.query(PlannedBlock).all()
        assert {b.task_id for b in blocks} == set(ids)  # the container itself is not scheduled
        assert planner.remaining_minutes(s, s.get(Task, pid)) == 270
    ex("complete_task", task_id=ids[0])
    with session() as s:
        assert s.get(Task, pid).progress == 50 and s.get(Task, pid).status != "done"
    ex("complete_task", task_id=ids[1])
    with session() as s:
        assert s.get(Task, pid).status == "done"


def test_exam_backward_plan():
    res = ex("plan_exam_prep", exams=[{"title": "ریاضی ۲", "date": "2026-10-29 09:00", "study_hours": 10,
                                       "topics": ["مشتق", "انتگرال", "سری"]}])
    sessions = res["exams"][0]["sessions"]
    assert len(sessions) == 4 and sessions[-1]["title"].endswith("مرور نهایی و نمونه سؤال")
    dues = [s_["due"] for s_ in sessions]
    assert dues == sorted(dues) and dues[-1] < "2026-10-29 09:00"
    with session() as s:
        assert s.query(PlannedBlock).count() > 0


def test_estimation_factor_learned_and_used():
    with session() as s:
        for i in range(3):
            t = Task(title=f"writing {i}", category="language", status="done", estimated_minutes=60,
                     completed_at=datetime(2026, 10, 10 + i, 20))
            s.add(t)
            s.flush()
            s.add(WorkLog(task_id=t.id, category="language", start_at=datetime(2026, 10, 10 + i, 18),
                          end_at=datetime(2026, 10, 10 + i, 19, 30), minutes=90))
        new = Task(title="writing new", category="language", estimated_minutes=60)
        s.add(new)
        s.flush()
        f = insights.estimation_factors(s)
        assert f["category"]["language"]["factor"] == 1.5
        assert planner.remaining_minutes(s, new) == 90
        assert "۱.۵" in " ".join(insights.estimation_lines(f)) or "1.5" in " ".join(insights.estimation_lines(f))


def test_peak_hours_and_energy_matching():
    with session() as s:
        for d in range(1, 15):  # lots of evening work -> evenings are the peak
            s.add(WorkLog(category="work", start_at=datetime(2026, 10, d, 19), end_at=datetime(2026, 10, d, 21),
                          minutes=120))
        peaks = insights.peak_hours(s)
        assert peaks["learned"] and set(peaks["hours"]) == {19, 20}
        s.add(Task(title="کار سبک", estimated_minutes=60, energy="low", due_at=datetime(2026, 10, 25, 23)))
        s.add(Task(title="مقاله سخت", estimated_minutes=60, energy="high", due_at=datetime(2026, 10, 25, 23)))
    with session() as s:
        res = planner.plan(s, days=3)
        hard = [b for b in res.blocks if b["title"] == "مقاله سخت"][0]
        assert hard["start"][11:13] in ("19", "20")


def test_locked_block_survives_replan_and_missed_blocks_are_swept(clock):
    tid = ex("add_task", title="الف", estimated_minutes=60, due="2026-10-25 20:00")["task"]["id"]
    with session() as s:
        b = s.query(PlannedBlock).filter(PlannedBlock.task_id == tid).first()
        bid = b.id
    ex("move_block", block_id=bid, start="2026-10-22 16:00")
    with session() as s:
        planner.plan(s, days=7)
        b = s.get(PlannedBlock, bid)
        assert b is not None and b.locked and b.start_at == datetime(2026, 10, 22, 16)
        assert s.query(PlannedBlock).filter(PlannedBlock.task_id == tid).count() == 1  # nothing extra needed
    clock.value = datetime(2026, 10, 22, 19, 30)
    with session() as s:
        assert planner.sweep_missed(s) == 1
        assert s.get(PlannedBlock, bid).status == "skipped"


def test_rescue_analysis_and_followup(clock):
    prefs.set("max_focus_minutes_per_day", 240)
    with session() as s:
        s.add(Task(title="پروژه فوری", category="university", priority=1, estimated_minutes=600,
                   due_at=datetime(2026, 10, 20, 18)))
        s.add(Task(title="مرتب کردن کمد", category="personal", priority=3, estimated_minutes=120))
    with session() as s:
        data = rescue.analyze(s)
    assert data["needed"] and data["shortfall_minutes"] > 0
    assert data["postpone_candidates"][0]["title"] == "مرتب کردن کمد"
    assert data["extension_candidates"][0]["who"] == "استاد"
    followups._last_rescue_check = None
    with session() as s:
        followups.generate(s)
        assert s.query(Followup).filter(Followup.kind == "rescue").count() == 1


# ---------------------------------------------------------------- habits, commitments, points
def test_habit_streaks(clock):
    hid = ex("add_habit", title="ورزش", unit="minutes", target=30, reminder_time="18:00")["habit"]["id"]
    with session() as s:
        for d in (16, 17, 18):
            s.add(HabitLog(habit_id=hid, day=date(2026, 10, d), value=40))
        s.add(HabitLog(habit_id=hid, day=date(2026, 10, 14), value=40))
    with session() as s:
        st = habits.habit_stats(s, s.get(Habit, hid))
    assert st["streak"] == 3 and st["best_streak"] == 3 and not st["met_now"]
    r = ex("log_habit", habit_id=hid, value=35)
    assert r["habit"]["streak"] == 4
    # the evening reminder is not sent once it's done
    clock.value = datetime(2026, 10, 19, 17, 59)
    with session() as s:
        followups.generate(s)
        assert s.query(Followup).filter(Followup.kind == "habit_reminder").count() == 1
    clock.value = datetime(2026, 10, 19, 18, 1)
    with session() as s:
        assert [f for f in followups.due_now(s) if f.kind == "habit_reminder"] == []


def test_commitments_auto_evaluated_and_closed(clock):
    c = ex("add_commitment", text="۲ ساعت رایتینگ", target_minutes=120, category="language")["commitment"]
    with session() as s:
        s.add(WorkLog(category="language", start_at=datetime(2026, 10, 19, 8), end_at=datetime(2026, 10, 19, 9, 30), minutes=90))
        out = habits.auto_evaluate(s, date(2026, 10, 19))
        assert out[0]["logged_minutes"] == 90 and out[0]["status"] == "pending"
    with session() as s:
        habits.close_past(s, date(2026, 10, 20))
        assert s.get(Commitment, c["id"]).status == "partial"


def test_points_and_weekly_score():
    with session() as s:
        s.add(WorkLog(category="work", start_at=datetime(2026, 10, 18, 9), end_at=datetime(2026, 10, 18, 13), minutes=240))
        s.add(Task(title="x", status="done", priority=1, due_at=datetime(2026, 10, 19, 12), completed_at=datetime(2026, 10, 18, 15)))
    with session() as s:
        pts = gamify.points_between(s, date(2026, 10, 17), date(2026, 10, 19))
        assert pts["focus"] == 24 and pts["tasks"] == 20
        lvl = gamify.level_for(350)
        assert lvl["level"] == 3 and lvl["next_level_at"] == 600
        score = gamify.weekly_score(s, date(2026, 10, 17))
        assert 0 < score["score"] <= 100 and "deadlines" in score["components"]


# ---------------------------------------------------------------- IELTS
def test_grade_writing_saves_score(fake_llm):
    fake_llm.script = [ChatResult(content="```json\n" + json.dumps({
        "criteria": {"task_response": {"band": 6.5}, "coherence_cohesion": {"band": 6.0},
                     "lexical_resource": {"band": 6.0}, "grammatical_range_accuracy": {"band": 5.5}},
        "overall": 9, "strengths": ["x"], "improvements": ["y"], "corrections": [], "vocabulary": []}) + "\n```")]
    res = ex("grade_ielts_writing", essay="Some people think that technology " * 30, task_type="task2")
    assert res["overall"] == 6.0  # recomputed from criteria, rounded down to half band
    assert fake_llm.calls[-1]["model"] == "fake-ielts"
    with session() as s:
        assert s.query(IeltsScore).one().skill == "writing"


def test_speaking_practice_switches_transcription_to_english(fake_llm):
    ex("start_speaking_practice", question="Describe a book you read recently", part=2)
    fake_llm.script = [ChatResult(content="خوب بود!")]
    run(core.handle_user_message("", "telegram", [core.Attachment("audio", b"\x00" * 3000, "audio/ogg", "v.ogg")]))
    assert fake_llm.last_language == "en"
    user_turn = fake_llm.calls[-1]["messages"][-1]["content"]
    assert "IELTS speaking practice answer" in user_turn


def test_vocab_leitner(clock):
    ex("add_vocab", words=[{"word": "mitigate", "meaning": "کاهش دادن"}, {"word": "mitigate"}])
    with session() as s:
        w = s.query(VocabWord).one()
        ielts.review(s, w, True)
        assert w.box == 2 and w.due == date(2026, 10, 21)
        ielts.review(s, w, False)
        assert w.box == 1 and w.due == date(2026, 10, 20)


# ---------------------------------------------------------------- reminders: snooze & adaptivity
def test_snooze_button_reschedules(clock, fake_llm):
    with session() as s:
        f = Followup(kind="deadline", due_at=datetime(2026, 10, 19, 9), status="sent", sent_at=datetime(2026, 10, 19, 9))
        s.add(f)
        s.flush()
        m = Message(role="assistant", content="یادت نره", channel="system", kind="deadline", meta={"followup_id": f.id})
        s.add(m)
        s.flush()
        mid = m.id
    text = actions.snooze_message(mid, 60)
    assert "۱۱:۰۰" in text
    with session() as s:
        new = s.query(Followup).filter(Followup.created_by == "snooze").one()
        assert new.due_at == datetime(2026, 10, 19, 11, 0) and new.status == "pending"


def test_adaptive_shift_and_response_tracking(clock):
    # reminders at 08:xx are ignored, at 06:xx answered -> new 08:00 reminders move earlier
    with session() as s:
        for d in range(1, 6):
            s.add(Followup(kind="deadline", due_at=datetime(2026, 10, d, 8), status="sent", sent_at=datetime(2026, 10, d, 8)))
            s.add(Followup(kind="deadline", due_at=datetime(2026, 10, d, 6), status="sent", sent_at=datetime(2026, 10, d, 6),
                           responded_at=datetime(2026, 10, d, 6, 5)))
        rs = insights.response_stats(s)
        assert rs["hours"][8]["rate"] == 0.0 and rs["hours"][6]["rate"] == 1.0
        shifted = insights.adjust_time(s, datetime(2026, 10, 21, 8, 0), "deadline", clock.value, rs)
        assert shifted == datetime(2026, 10, 21, 6, 0)
        assert insights.reminder_lines(rs)
    # a user message marks recent sent follow-ups as answered
    with session() as s:
        s.add(Followup(kind="idle_checkin", due_at=datetime(2026, 10, 19, 9, 30), status="sent",
                       sent_at=datetime(2026, 10, 19, 9, 30)))
    from app import notify

    notify.store_message("user", "سلام", "web")
    with session() as s:
        assert s.query(Followup).filter(Followup.kind == "idle_checkin").one().responded_at is not None


def test_weekly_planning_and_vocab_followups(clock, fake_llm):
    prefs.set("weekly_planning_day", "mon")
    prefs.set("weekly_planning_time", "10:30")
    prefs.set("vocab_review_time", "10:40")
    ex("add_vocab", words=[{"word": "abundant"}])
    with session() as s:
        followups.generate(s)
        kinds = {f.kind for f in s.query(Followup)}
        assert {"weekly_planning", "vocab_review"} <= kinds
    clock.value = datetime(2026, 10, 19, 10, 45)
    fake_llm.default = "پیام"
    run(proactive.tick())
    with session() as s:
        sent = {m.kind for m in s.query(Message)}
        assert {"weekly_planning", "vocab_review"} <= sent
        vm = s.query(Message).filter(Message.kind == "vocab_review").one()
        assert vm.meta["followup_id"]


# ---------------------------------------------------------------- delivery: outbox, documents, forwards, push
class FlakyBot(telegram.TelegramBot):
    def __init__(self, fail_times=1):
        super().__init__("T", "https://api.telegram.org")
        self.fail_times = fail_times
        self.sent = []

    async def call(self, method, **params):
        if method == "sendMessage" and self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("network down")
        self.sent.append({"method": method, **params})
        return {"message_id": 100 + len(self.sent)}


def test_outbox_retries_until_delivered(monkeypatch, clock):
    bot = FlakyBot(fail_times=1)
    monkeypatch.setattr(telegram, "bot", bot)
    prefs.set("telegram_chat_id", 42)
    assert run(outbox.send("text", {"text": "سلام"})) is None
    with session() as s:
        row = s.query(Outbox).one()
        assert row.status == "pending" and row.attempts == 1
    clock.value = clock.value + timedelta(minutes=1)
    assert run(outbox.flush()) == 1
    with session() as s:
        assert s.query(Outbox).one().status == "sent"


def test_forwarded_message_and_document(monkeypatch, fake_llm):
    bot = FlakyBot(fail_times=0)
    monkeypatch.setattr(telegram, "bot", bot)
    prefs.set("telegram_chat_id", 42)

    async def download(file_id):
        return "تکلیف: تمرین‌های فصل ۴ تا شنبه".encode()

    bot.download = download
    fake_llm.script = [ChatResult(content="ثبت کردم")]
    run(bot.handle({"message": {
        "chat": {"id": 42, "type": "private"}, "caption": "",
        "forward_origin": {"type": "chat", "sender_chat": {"title": "گروه پایگاه داده"}},
        "document": {"file_id": "f1", "mime_type": "text/plain", "file_name": "hw.txt", "file_size": 50}}}))
    turn = fake_llm.calls[-1]["messages"][-1]["content"]
    assert "گروه پایگاه داده" in turn and "فصل ۴" in turn


def test_pdf_text_extraction():
    pdf = _tiny_pdf("Homework 3 due Saturday")
    text = core.extract_document_text(core.Attachment("document", pdf, "application/pdf", "hw.pdf"))
    assert "Homework 3" in text


def _tiny_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def test_push_keys_and_subscriptions():
    priv, pub = push.vapid_keys()
    assert "BEGIN PRIVATE KEY" in priv and len(pub) > 80
    assert push.vapid_keys() == (priv, pub)  # stable
    push.subscribe({"endpoint": "https://push.example/1", "keys": {"p256dh": "a", "auth": "b"}})
    push.subscribe({"endpoint": "https://push.example/1", "keys": {"p256dh": "c", "auth": "d"}})
    assert push.count() == 1
    with pytest.raises(ValueError):
        push.subscribe({"endpoint": "x"})


def test_voice_reply_sent_when_user_used_voice(monkeypatch, fake_llm):
    bot = FlakyBot(fail_times=0)
    sent_voice = []

    async def send_voice(chat_id, audio, caption=None):
        sent_voice.append(audio)
        return 7

    bot.send_voice = send_voice
    monkeypatch.setattr(telegram, "bot", bot)
    prefs.set("telegram_chat_id", 42)

    async def download(file_id):
        return b"\x00" * 3000

    bot.download = download
    fake_llm.script = [ChatResult(content="باشه، ثبت شد")]
    run(bot.handle({"message": {"chat": {"id": 42, "type": "private"}, "voice": {"file_id": "v", "mime_type": "audio/ogg"}}}))
    assert sent_voice == [b"OggS"]


def test_api_new_endpoints(fake_llm):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        h = c.post("/api/habits", json={"title": "مطالعه", "unit": "minutes", "target": 20}).json()["habit"]
        assert c.post(f"/api/habits/{h['id']}/log", json={"value": 25}).json()["habit"]["streak"] == 1
        assert c.get("/api/habits").json()["habits"][0]["today"] == 25
        assert c.post("/api/commitments", json={"text": "۱ ساعت کار"}).status_code == 200
        assert c.get("/api/stats/heatmap?days=30").json()["days"][-1]["date"] == "2026-10-19"
        ins = c.get("/api/insights").json()
        assert ins["game"]["level"] >= 1 and len(ins["hour_profile"]) == 24
        assert "budget" in c.get("/api/usage").json()
        assert c.post("/api/vocab", json={"words": [{"word": "robust"}]}).status_code == 200
        assert c.get("/api/ielts").json()["vocab"]["total"] == 1
        dash = c.get("/api/dashboard").json()
        assert "game" in dash and "habits" in dash and "commitments" in dash
        # UI deletions are trusted (the UI shows its own confirm dialog)
        t = c.post("/api/tasks", json={"title": "x"}).json()["task"]
        assert c.delete(f"/api/tasks/{t['id']}").json() == {"deleted": t["id"]}
        key = c.get("/api/push/key").json()["public_key"]
        assert key


def test_eval_harness_runs_with_scripted_model(tmp_path, fake_llm):
    from app import evals

    def by_model(messages, tools):
        assert fake_llm.calls[-1]["model"] == "model-x"
        return call("add_event", title="دندون‌پزشکی", start="1405-08-05 10:00", duration_minutes=60)

    fake_llm.script = [by_model, ChatResult(content="✅ دندون‌پزشکی سه‌شنبه ۵ آبان ساعت ۱۰ ثبت شد")]
    jalali = next(s for s in evals.SCENARIOS if s.name == "jalali")
    res = run(evals.run_scenario(jalali, "model-x"))
    assert res["passed"] == res["total"] == 2
    report = evals.render([res])
    assert "model-x" in report and "2/2" in report
