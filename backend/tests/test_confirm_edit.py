"""✅/❌ buttons that run the change right away, and corrections that edit instead of duplicating."""

import asyncio

from conftest import call
from fastapi.testclient import TestClient

from app import outbox, pending
from app.agent import core, tools
from app.db import Event, Message, PendingAction, Task, session
from app.llm import ChatResult


def run(c):
    return asyncio.run(c)


def ex(_tool, **args):
    ctx = tools.Ctx(channel="web", turn_id="t-test")
    res = run(tools.execute(_tool, args, ctx))
    tools.finalize(ctx)
    return res


def _task(title="گزارش هفتگی شرکت", **kw):
    return ex("add_task", title=title, **kw)["task"]["id"]


# ---------------------------------------------------------------- yes / no detection
def test_quick_answer():
    for t in ("آره", "آره حذفش کن", "باشه", "اوکی", "بله", "ok", "تایید", "باشه ممنون", "آره، پاکش کن!"):
        assert pending.quick_answer(t) is True, t
    for t in ("نه", "نه ممنون", "ولش کن", "بیخیال", "لغو"):
        assert pending.quick_answer(t) is False, t
    for t in ("آره ولی فردا", "باشه ساعت ۶ بذارش", "آره فقط اون اولی", "فردا ساعت ۵ جلسه دارم", ""):
        assert pending.quick_answer(t) is None, t


# ---------------------------------------------------------------- buttons
def test_delete_shows_buttons_and_approve_runs_it_without_the_model(fake_llm):
    from app.main import create_app

    tid = _task()
    fake_llm.script = [call("delete_task", task_id=tid), ChatResult(content="«گزارش هفتگی شرکت» حذف بشه؟")]
    _, reply = run(core.handle_user_message("گزارش هفتگی رو پاک کن", "web"))
    confirm = reply["meta"]["confirm"]
    assert confirm["status"] == "pending" and "گزارش هفتگی شرکت" in confirm["items"][0]
    assert not reply["meta"]["actions"]  # no red "failed" chip for the waiting delete
    rows = outbox.buttons_for(reply["id"], reply["meta"])
    assert [b["data"] for b in rows[0]] == [f"pa:{reply['id']}:y", f"pa:{reply['id']}:n"]
    with session() as s:
        assert s.get(Task, tid) is not None

    calls = len(fake_llm.calls)
    client = TestClient(create_app())
    r = client.post(f"/api/messages/{reply['id']}/approve").json()
    assert r["ok"] and "حذف تسک «گزارش هفتگی شرکت»" in r["text"]
    assert len(fake_llm.calls) == calls  # no model call
    with session() as s:
        assert s.get(Task, tid) is None
        m = s.get(Message, reply["id"])
        assert m.meta["confirm"]["status"] == "approved" and m.meta["undoable"]
        last = s.query(Message).order_by(Message.id.desc()).first()
        assert "after the user pressed ✅" in last.meta["action_log"][0]
    # pressing again does nothing
    assert not client.post(f"/api/messages/{reply['id']}/approve").json()["ok"]
    # the original turn's undo brings the task back
    from app import actions

    actions.undo_message(reply["id"])
    with session() as s:
        assert s.get(Task, tid) is not None


def test_typed_yes_and_no_answer_the_buttons_without_the_model(fake_llm):
    tid = _task()
    fake_llm.script = [call("delete_task", task_id=tid), ChatResult(content="حذفش کنم؟")]
    run(core.handle_user_message("پاکش کن", "telegram"))
    calls = len(fake_llm.calls)
    _, reply = run(core.handle_user_message("نه", "telegram"))
    assert len(fake_llm.calls) == calls and reply["content"] == "باشه، دست نزدم."
    with session() as s:
        assert s.get(Task, tid) is not None

    fake_llm.script = [call("delete_task", task_id=tid), ChatResult(content="حذفش کنم؟")]
    run(core.handle_user_message("پاکش کن", "telegram"))
    _, reply = run(core.handle_user_message("آره حذفش کن", "telegram"))
    assert "✅ انجام شد" in reply["content"]
    with session() as s:
        assert s.get(Task, tid) is None


def test_yes_without_pending_buttons_goes_to_the_model(fake_llm):
    fake_llm.script = [ChatResult(content="چی رو؟")]
    _, reply = run(core.handle_user_message("آره", "web"))
    assert reply["content"] == "چی رو؟"


def test_a_new_proposal_replaces_the_unanswered_one(fake_llm):
    a, b = _task("کار اول"), _task("کار دوم")
    fake_llm.script = [call("delete_task", task_id=a), ChatResult(content="؟")]
    _, first = run(core.handle_user_message("اولی رو پاک کن", "web"))
    fake_llm.script = [call("delete_task", task_id=b), ChatResult(content="؟")]
    run(core.handle_user_message("نه دومی رو پاک کن", "web"))
    assert not run(pending.resolve(first["id"], True))["ok"]
    with session() as s:
        assert s.get(Task, a) is not None
        assert {r.status for r in s.query(PendingAction).all()} == {"expired", "pending"}


def test_ask_approval_bundles_changes_and_runs_them_on_approve(fake_llm):
    a, b = _task("کار اول", due="2026-11-05 18:00"), _task("کار دوم", due="2026-11-05 18:00")
    fake_llm.script = [
        call("ask_approval", summary="هر دو کار را به شنبه منتقل کنم", actions=[
            {"tool": "update_task", "args": {"task_id": a, "due": "2026-11-07 18:00"}},
            {"tool": "update_task", "args": {"task_id": b, "due": "2026-11-07 18:00"}},
        ]),
        ChatResult(content="پیشنهادم اینه که هر دو رو ببرم شنبه."),
    ]
    _, reply = run(core.handle_user_message("وقت ندارم این هفته", "web"))
    assert reply["meta"]["confirm"]["summary"] == "هر دو کار را به شنبه منتقل کنم"
    assert len(reply["meta"]["confirm"]["items"]) == 2
    with session() as s:
        assert s.get(Task, a).due_at.day == 5
    res = run(pending.resolve(reply["id"], True))
    assert res["ok"] and res["text"].count("✅") == 2
    with session() as s:
        assert s.get(Task, a).due_at.day == 7 and s.get(Task, b).due_at.day == 7


def test_ask_approval_rejects_bad_proposals():
    tid = _task()
    assert "error" in ex("ask_approval", summary="x", actions=[{"tool": "list_tasks", "args": {}}])
    assert "error" in ex("ask_approval", summary="x", actions=[{"tool": "nope", "args": {}}])
    assert "does not exist" in ex("ask_approval", summary="x", actions=[{"tool": "delete_task", "args": {"task_id": 999}}])["error"]
    assert "missing" in ex("ask_approval", summary="x", actions=[{"tool": "add_task", "args": {}}])["error"]
    assert ex("ask_approval", summary="x", actions=[{"tool": "delete_task", "args": {"task_id": tid}}])["buttons_shown"]


def test_typed_confirmation_through_the_model_still_works(fake_llm):
    tid = _task()
    fake_llm.script = [call("delete_task", task_id=tid), ChatResult(content="حذف بشه؟")]
    run(core.handle_user_message("پاکش کن", "web"))
    fake_llm.script = [call("delete_task", task_id=tid, confirmed=True), ChatResult(content="حذف شد")]
    run(core.handle_user_message("آره مطمئنم ولی سریع", "web"))  # not a bare yes → goes to the model
    with session() as s:
        assert s.get(Task, tid) is None


# ---------------------------------------------------------------- edits instead of duplicates
def test_adding_the_same_task_again_points_to_the_existing_one():
    tid = _task("ارسال گزارش پروژه ماشین", due="2026-11-05 18:00")
    r = ex("add_task", title="ارسال گزارش پروژه ماشین", due="2026-11-06 12:00")
    assert "NOT added" in r["error"] and r["existing_task"]["id"] == tid
    with session() as s:
        assert s.query(Task).count() == 1
    # a different week's homework or a numbered part is not a duplicate
    assert "task" in ex("add_task", title="ارسال گزارش پروژه ماشین", due="2026-11-20 18:00")
    assert "task" in ex("add_task", title="فصل ۲ کتاب", due="2026-11-05 18:00")
    assert "task" in ex("add_task", title="فصل ۳ کتاب", due="2026-11-05 18:00")
    # explicitly separate
    assert "task" in ex("add_task", title="ارسال گزارش پروژه ماشین", due="2026-11-05 20:00", allow_duplicate=True)


def test_update_and_complete_by_title():
    tid = _task("تمرین سری سوم آمار", due="2026-11-05 18:00")
    _task("خرید هدیه تولد")
    r = ex("update_task", task="تمرین آمار", due="2026-11-06 10:00")
    assert r["task"]["id"] == tid and r["task"]["due"].startswith("2026-11-06")
    r = ex("complete_task", task="سری سوم آمار")
    assert r["task"]["status"] == "done"
    assert "no task matches" in ex("update_task", task="چیزی که نیست", priority=1)["error"]
    assert "which task" in ex("update_task", priority=1)["error"]


def test_ambiguous_title_asks_for_the_id():
    _task("تمرین فیزیک فصل اول بخش الف")
    _task("تمرین فیزیک فصل اول بخش ب")
    r = ex("update_task", task="تمرین فیزیک فصل اول", priority=1)
    assert "several tasks match" in r["error"]


def test_event_correction_updates_instead_of_adding(clock):
    ev = ex("add_event", title="جلسه با مدیر پروژه", start="2026-11-03 17:00")["event"]["id"]
    r = ex("add_event", title="جلسه با مدیر پروژه", start="2026-11-03 18:00")
    assert r["existing_event"]["id"] == ev
    r = ex("update_event", event="جلسه مدیر پروژه", start="2026-11-03 18:00")
    assert r["event"]["id"] == ev
    with session() as s:
        assert s.query(Event).count() == 1


def test_context_lists_recent_items_and_history_has_titles(fake_llm):
    fake_llm.script = [call("add_task", title="خرید کتاب IELTS", due="2026-11-05 18:00"), ChatResult(content="ثبت شد")]
    run(core.handle_user_message("باید کتاب آیلتس بخرم", "web"))
    fake_llm.script = [ChatResult(content="باشه")]
    run(core.handle_user_message("ددلاینش رو بکن جمعه", "web"))
    msgs = fake_llm.calls[-1]["messages"]
    assert "Recently added/changed" in msgs[1]["content"] and "خرید کتاب IELTS" in msgs[1]["content"]
    assert any("«خرید کتاب IELTS»" in m["content"] for m in msgs if m["role"] == "assistant")
