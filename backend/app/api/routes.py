"""REST + SSE API for the web UI."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import Response as RawResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Integer

from .. import (actions, cal, gamify, habits, ielts, insights, notify, outbox, planner, prefs, push, reports,
               serialize, stats, telegram, timeutil)
from ..agent import core, tools
from ..bus import bus
from ..config import get_settings
from ..db import (Commitment, Followup, Goal, Habit, IeltsScore, LlmUsage, Memory, Message, PlannedBlock,
                  RecurringEvent, Report, Task, VocabWord, WorkLog, session)
from ..llm import LLMError, get_llm
from . import auth

public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_auth)])


# ---------------------------------------------------------------- auth
class LoginBody(BaseModel):
    password: str


@public.post("/login")
def login(body: LoginBody, response: Response):
    if not auth.auth_required():
        return {"ok": True}
    if not auth.check_password(body.password):
        raise HTTPException(401, "رمز اشتباه است")
    response.set_cookie(auth.COOKIE, auth.make_token(), max_age=auth.MAX_AGE, httponly=True, samesite="lax")
    return {"ok": True}


@public.post("/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@public.get("/me")
def me(request: Request):
    ok = auth.is_authenticated(request)
    from ..config import version

    out: dict[str, Any] = {"auth_required": auth.auth_required(), "authenticated": ok, "now": timeutil.iso(timeutil.now()),
                           "version": version().get("short")}
    if ok:
        p = prefs.all_prefs()
        out.update(
            {
                "llm_configured": bool(getattr(get_llm(), "configured", True)),
                "user_name": p.get("user_name"),
                "assistant_name": p.get("assistant_name"),
                "telegram": telegram.status(),
            }
        )
    return out


# ---------------------------------------------------------------- helpers
async def _tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    # explicit clicks in the UI: the UI already asked for confirmation
    ctx = tools.Ctx(channel="web", trusted=True)
    res = await tools.execute(name, args, ctx)
    if "error" in res:
        raise HTTPException(400, res["error"])
    tools.finalize(ctx)
    if ctx.changed:
        bus.publish("data_changed", sorted(ctx.changed))
    return res


# ---------------------------------------------------------------- chat
@router.get("/messages")
def messages(before_id: int | None = None, limit: int = 60):
    with session() as s:
        q = s.query(Message)
        if before_id:
            q = q.filter(Message.id < before_id)
        rows = q.order_by(Message.id.desc()).limit(min(limit, 200)).all()
        return {"messages": [serialize.message(m) for m in reversed(rows)]}


@router.post("/chat")
async def chat(
    background: BackgroundTasks,
    text: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    attachments = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        mime = f.content_type or "application/octet-stream"
        name = (f.filename or "").lower()
        kind = (
            "image" if mime.startswith("image/")
            else "audio" if mime.startswith(("audio/", "video/webm"))
            else "document" if mime == "application/pdf" or mime.startswith("text/") or name.endswith((".pdf", ".txt", ".md", ".csv"))
            else None
        )
        if kind is None:
            raise HTTPException(400, f"نوع فایل پشتیبانی نمی‌شود: {mime}")
        attachments.append(core.Attachment(kind, data, mime, f.filename or ""))
    if not text.strip() and not attachments:
        raise HTTPException(400, "پیام خالی است")
    user_msg, reply = await core.handle_user_message(text, "web", attachments)
    background.add_task(notify.mirror_web_turn, user_msg["content"] or "📎", reply)
    return {"user": user_msg, "assistant": reply}


@router.post("/messages/{message_id}/undo")
def undo_message(message_id: int):
    return {"text": actions.undo_message(message_id)}


@router.post("/messages/{message_id}/snooze")
def snooze_message(message_id: int, minutes: int = 60):
    return {"text": actions.snooze_message(message_id, minutes)}


@router.get("/messages/{message_id}/tts")
async def message_tts(message_id: int):
    from ..llm import get_llm

    with session() as s:
        m = s.get(Message, message_id)
        if m is None:
            raise HTTPException(404, "not found")
        text = notify.plain_text(m.content)[:2000]
    try:
        audio = await get_llm().speak(text, fmt="mp3")
    except LLMError as e:
        raise HTTPException(502, str(e)) from e
    return RawResponse(content=audio, media_type="audio/mpeg")


@router.get("/stream")
async def stream(request: Request):
    q = bus.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- dashboard
@router.get("/dashboard")
def dashboard():
    with session() as s:
        now = timeutil.now()
        today = now.date()
        p = prefs.all_prefs(s)
        open_tasks = sorted(planner.open_tasks(s), key=planner._task_key)
        week = stats.productivity(s, timeutil.week_start(today), timeutil.week_start(today) + timedelta(days=6))
        upcoming = (
            s.query(Followup).filter(Followup.status == "pending", Followup.due_at >= now)
            .order_by(Followup.due_at).limit(6).all()
        )
        feas = planner.feasibility(s)
        return {
            "now": timeutil.iso(now),
            "today_label": timeutil.fa_date(today, with_year=True),
            "user_name": p.get("user_name"),
            "agenda": cal.agenda(s, today, 2),
            "tasks": [serialize.task(s, t) for t in open_tasks[:12]],
            "open_count": len(open_tasks),
            "overdue_count": sum(1 for t in open_tasks if t.due_at and t.due_at < now),
            "today": stats.today_summary(s),
            "week": {k: week[k] for k in ("days", "by_category", "total_productive_minutes", "daily_goal_minutes",
                                          "average_per_day_minutes", "blocks")}
            | {"completed": len(week["completed_tasks"])},
            "focus": stats.focus_to_dict(s, stats.active_focus(s)),
            "goals": [serialize.goal(g) for g in s.query(Goal).filter(Goal.status == "active").all()],
            "followups": [serialize.followup(f) for f in upcoming],
            "workload": {"at_risk": feas.at_risk, "min_slack": feas.min_slack, "total_required": feas.total_required,
                         "capacity_7d": feas.capacity_7d},
            "game": gamify.summary(s),
            "habits": habits.all_habits(s),
            "commitments": habits.auto_evaluate(s, today),
            "budget": get_llm().budget() if hasattr(get_llm(), "budget") else None,
        }


@router.get("/agenda")
def agenda(start: str | None = None, days: int = 7):
    with session() as s:
        d = timeutil.parse_date(start) if start else timeutil.week_start(timeutil.today())
        return {"days": cal.agenda(s, d, max(1, min(days, 42)))}


# ---------------------------------------------------------------- tasks
@router.get("/tasks")
def list_tasks(status: str = "open", category: str | None = None, project_id: int | None = None):
    with session() as s:
        q = s.query(Task)
        if project_id:
            q = q.filter(Task.project_id == project_id)
        if status == "open":
            q = q.filter(Task.status.in_(["todo", "doing"]))
        elif status == "done":
            q = q.filter(Task.status == "done")
        if category:
            q = q.filter(Task.category == category)
        rows = q.order_by(Task.status == "done", Task.due_at.is_(None), Task.due_at, Task.priority, Task.id.desc()).limit(300).all()
        return {"tasks": [serialize.task(s, t) for t in rows]}


@router.post("/tasks")
async def create_task(body: dict[str, Any]):
    return await _tool("add_task", body)


@router.patch("/tasks/{task_id}")
async def patch_task(task_id: int, body: dict[str, Any]):
    if body.get("status") == "done" and len(body) == 1:
        return await _tool("complete_task", {"task_id": task_id})
    return await _tool("update_task", {**body, "task_id": task_id})


@router.delete("/tasks/{task_id}")
async def remove_task(task_id: int):
    return await _tool("delete_task", {"task_id": task_id})


# ---------------------------------------------------------------- recurring & events
@router.get("/recurring")
def list_recurring():
    with session() as s:
        return {"recurring": [serialize.recurring(r) for r in s.query(RecurringEvent).order_by(RecurringEvent.id).all()]}


@router.post("/recurring")
async def create_recurring(body: dict[str, Any]):
    return await _tool("add_recurring_events", {"events": [body]})


@router.patch("/recurring/{rec_id}")
async def patch_recurring(rec_id: int, body: dict[str, Any]):
    return await _tool("update_recurring_event", {**body, "recurring_event_id": rec_id})


@router.delete("/recurring/{rec_id}")
async def remove_recurring(rec_id: int):
    return await _tool("delete_recurring_event", {"recurring_event_id": rec_id})


@router.post("/events")
async def create_event(body: dict[str, Any]):
    return await _tool("add_event", body)


@router.patch("/events/{event_id}")
async def patch_event(event_id: int, body: dict[str, Any]):
    return await _tool("update_event", {**body, "event_id": event_id})


@router.delete("/events/{event_id}")
async def remove_event(event_id: int):
    return await _tool("delete_event", {"event_id": event_id})


class PlanBody(BaseModel):
    days: int = 7


class BlockBody(BaseModel):
    start: str
    duration_minutes: int | None = None
    task_id: int | None = None


@router.patch("/blocks/{block_id}")
async def move_block(block_id: int, body: BlockBody):
    return await _tool("move_block", {"block_id": block_id, "start": body.start, "duration_minutes": body.duration_minutes})


@router.post("/blocks")
def create_block(body: BlockBody):
    if not body.task_id:
        raise HTTPException(400, "task_id لازم است")
    with session() as s:
        if s.get(Task, body.task_id) is None:
            raise HTTPException(404, "تسک پیدا نشد")
        st = timeutil.parse_datetime(body.start)
        b = PlannedBlock(task_id=body.task_id, start_at=st, end_at=st + timedelta(minutes=body.duration_minutes or 60),
                         locked=True)
        s.add(b)
        s.flush()
        bid = b.id
    bus.publish("data_changed", ["schedule", "followups"])
    return {"id": bid}


@router.delete("/blocks/{block_id}")
def delete_block(block_id: int):
    with session() as s:
        b = s.get(PlannedBlock, block_id)
        if b:
            s.delete(b)
    bus.publish("data_changed", ["schedule", "followups"])
    return {"deleted": block_id}


@router.post("/plan")
async def run_plan(body: PlanBody):
    return await _tool("plan_schedule", {"days": body.days})


class AvailabilityBody(BaseModel):
    start: str
    end: str | None = None
    duration_minutes: int | None = None
    buffer_minutes: int = 30


@router.post("/availability")
async def availability(body: AvailabilityBody):
    return await _tool("check_availability", body.model_dump(exclude_none=True))


# ---------------------------------------------------------------- focus & stats
@router.post("/focus/start")
async def focus_start(body: dict[str, Any]):
    return await _tool("start_focus", body)


@router.post("/focus/stop")
async def focus_stop(body: dict[str, Any]):
    return await _tool("stop_focus", body)


@router.get("/stats")
def get_stats(start: str | None = None, end: str | None = None):
    with session() as s:
        today = timeutil.today()
        a = timeutil.parse_date(start) if start else timeutil.week_start(today)
        b = timeutil.parse_date(end) if end else a + timedelta(days=6)
        return stats.productivity(s, a, b)


@router.get("/stats/heatmap")
def heatmap(days: int = 371):
    from sqlalchemy import func

    with session() as s:
        today = timeutil.today()
        start = today - timedelta(days=max(7, min(days, 800)) - 1)
        rows = (
            s.query(func.date(WorkLog.start_at), func.sum(WorkLog.minutes))
            .filter(WorkLog.start_at >= timeutil.at(start, "00:00"), WorkLog.productive.is_(True), WorkLog.end_at.is_not(None))
            .group_by(func.date(WorkLog.start_at))
            .all()
        )
        by_day = {str(d): int(m or 0) for d, m in rows}
        out, d = [], start
        while d <= today:
            out.append({"date": d.isoformat(), "jalali": timeutil.jalali_str(d), "minutes": by_day.get(d.isoformat(), 0)})
            d += timedelta(days=1)
        return {"days": out, "goal_minutes": int(prefs.get("daily_focus_goal_minutes", s=s) or 240)}


@router.get("/insights")
def get_insights():
    with session() as s:
        factors = insights.estimation_factors(s)
        peaks = insights.peak_hours(s)
        rs = insights.response_stats(s)
        return {
            "estimation": factors,
            "estimation_text": insights.estimation_lines(factors),
            "peak_hours": peaks["hours"],
            "peak_hours_learned": peaks["learned"],
            "hour_profile": peaks["profile"],
            "reminders": rs,
            "reminders_text": insights.reminder_lines(rs),
            "game": gamify.summary(s),
        }


@router.get("/usage")
def usage(days: int = 30):
    from sqlalchemy import func

    with session() as s:
        since = timeutil.today() - timedelta(days=days - 1)
        by_day = (
            s.query(LlmUsage.day, func.sum(LlmUsage.cost_usd), func.sum(LlmUsage.prompt_tokens),
                    func.sum(LlmUsage.completion_tokens), func.count(LlmUsage.id))
            .filter(LlmUsage.day >= since).group_by(LlmUsage.day).order_by(LlmUsage.day).all()
        )
        by_model = (
            s.query(LlmUsage.model, func.sum(LlmUsage.cost_usd), func.sum(LlmUsage.prompt_tokens),
                    func.sum(LlmUsage.completion_tokens), func.count(LlmUsage.id),
                    func.sum(func.cast(LlmUsage.ok == False, Integer)))  # noqa: E712
            .filter(LlmUsage.day >= since).group_by(LlmUsage.model).all()
        )
        by_purpose = (
            s.query(LlmUsage.purpose, func.sum(LlmUsage.cost_usd), func.count(LlmUsage.id))
            .filter(LlmUsage.day >= since).group_by(LlmUsage.purpose).all()
        )
        return {
            "budget": get_llm().budget() if hasattr(get_llm(), "budget") else None,
            "days": [{"day": d.isoformat(), "cost": round(c or 0, 4), "prompt_tokens": int(p or 0),
                      "completion_tokens": int(ct or 0), "calls": n} for d, c, p, ct, n in by_day],
            "models": sorted([{"model": m, "cost": round(c or 0, 4), "prompt_tokens": int(p or 0),
                               "completion_tokens": int(ct or 0), "calls": n, "failures": int(f or 0)}
                              for m, c, p, ct, n, f in by_model], key=lambda x: -x["cost"]),
            "purposes": sorted([{"purpose": pu, "cost": round(c or 0, 4), "calls": n} for pu, c, n in by_purpose],
                               key=lambda x: -x["cost"]),
            "total_cost": round(sum((c or 0) for _, c, *_ in by_day), 4),
        }


# ---------------------------------------------------------------- projects & supervisor contacts
@router.get("/projects")
def list_projects():
    from .. import projects as pm
    from ..db import Project

    with session() as s:
        return {"projects": [pm.project_dict(s, p) for p in s.query(Project).order_by(Project.id).all()]}


@router.post("/projects")
async def create_project(body: dict[str, Any]):
    return await _tool("add_project", body)


@router.patch("/projects/{project_id}")
async def patch_project(project_id: int, body: dict[str, Any]):
    from ..db import Project

    aliases = body.pop("aliases", None)
    res = await _tool("update_project", {**body, "project": str(project_id)})
    if aliases is not None:
        with session() as s:
            p = s.get(Project, project_id)
            p.aliases = [a.strip() for a in aliases if str(a).strip()]
        bus.publish("data_changed", ["projects"])
    return res


@router.delete("/projects/{project_id}")
def remove_project(project_id: int):
    from ..db import Project

    with session() as s:
        p = s.get(Project, project_id)
        if p:
            s.delete(p)
    bus.publish("data_changed", ["projects", "tasks"])
    return {"deleted": project_id}


@router.get("/contacts")
def list_contacts():
    from .. import projects as pm

    with session() as s:
        return pm.pipeline(s)


@router.post("/contacts")
async def create_contact(body: dict[str, Any]):
    return await _tool("add_contact", body)


@router.patch("/contacts/{contact_id}")
async def patch_contact(contact_id: int, body: dict[str, Any]):
    return await _tool("update_contact", {**body, "contact": str(contact_id)})


@router.delete("/contacts/{contact_id}")
def remove_contact(contact_id: int):
    from ..db import Contact

    with session() as s:
        c = s.get(Contact, contact_id)
        if c:
            s.delete(c)
    bus.publish("data_changed", ["contacts"])
    return {"deleted": contact_id}


# ---------------------------------------------------------------- habits & commitments
@router.get("/habits")
def list_habits():
    with session() as s:
        return {"habits": habits.all_habits(s, active_only=False)}


@router.post("/habits")
async def create_habit(body: dict[str, Any]):
    return await _tool("add_habit", body)


@router.patch("/habits/{habit_id}")
async def patch_habit(habit_id: int, body: dict[str, Any]):
    return await _tool("update_habit", {**body, "habit_id": habit_id})


@router.delete("/habits/{habit_id}")
async def remove_habit(habit_id: int):
    return await _tool("delete_habit", {"habit_id": habit_id})


@router.post("/habits/{habit_id}/log")
async def log_habit(habit_id: int, body: dict[str, Any]):
    return await _tool("log_habit", {**body, "habit_id": habit_id})


@router.get("/commitments")
def list_commitments(day: str | None = None):
    with session() as s:
        d = timeutil.parse_date(day) if day else timeutil.today()
        return {"day": d.isoformat(), "commitments": habits.auto_evaluate(s, d)}


@router.post("/commitments")
async def create_commitment(body: dict[str, Any]):
    return await _tool("add_commitment", body)


@router.patch("/commitments/{commitment_id}")
async def patch_commitment(commitment_id: int, body: dict[str, Any]):
    return await _tool("update_commitment", {**body, "commitment_id": commitment_id})


# ---------------------------------------------------------------- IELTS
@router.get("/ielts")
def ielts_overview():
    with session() as s:
        return ielts.progress(s)


@router.post("/ielts/scores")
async def add_ielts_score(body: dict[str, Any]):
    return await _tool("log_ielts_score", body)


@router.delete("/ielts/scores/{score_id}")
def remove_ielts_score(score_id: int):
    with session() as s:
        sc = s.get(IeltsScore, score_id)
        if sc:
            s.delete(sc)
    bus.publish("data_changed", ["ielts"])
    return {"deleted": score_id}


@router.get("/vocab")
def list_vocab(due: bool = False):
    with session() as s:
        rows = ielts.due_words(s, 200) if due else s.query(VocabWord).order_by(VocabWord.box, VocabWord.due).all()
        return {"words": [ielts.word_dict(w) for w in rows], "stats": ielts.vocab_stats(s)}


@router.post("/vocab")
async def add_vocab(body: dict[str, Any]):
    return await _tool("add_vocab", body)


@router.post("/vocab/{word_id}/review")
async def review_vocab(word_id: int, body: dict[str, Any]):
    return await _tool("vocab_review", {"word_id": word_id, "correct": bool(body.get("correct"))})


@router.delete("/vocab/{word_id}")
def remove_vocab(word_id: int):
    with session() as s:
        w = s.get(VocabWord, word_id)
        if w:
            s.delete(w)
    bus.publish("data_changed", ["ielts"])
    return {"deleted": word_id}


# ---------------------------------------------------------------- web push
@router.get("/push/key")
def push_key():
    return {"public_key": push.vapid_keys()[1], "subscriptions": push.count()}


@router.post("/push/subscribe")
def push_subscribe(body: dict[str, Any], request: Request):
    try:
        push.subscribe(body, request.headers.get("user-agent", ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "subscriptions": push.count()}


@router.post("/push/unsubscribe")
def push_unsubscribe(body: dict[str, Any]):
    push.unsubscribe(str(body.get("endpoint", "")))
    return {"ok": True}


@router.post("/push/test")
async def push_test():
    n = await push.notify_all("پیگیر", "🔔 اعلان آزمایشی — نوتیفیکیشن مرورگر کار می‌کند.", tag="test")
    if not n:
        raise HTTPException(400, "هیچ مرورگری مشترک نیست یا ارسال ناموفق بود")
    return {"sent": n}


# ---------------------------------------------------------------- reports
@router.get("/reports")
def list_reports():
    with session() as s:
        return {"reports": [serialize.report(r) for r in s.query(Report).order_by(Report.week_start.desc()).all()]}


class ReportBody(BaseModel):
    week_offset: int = 0
    project_id: int | None = None


@router.post("/reports/generate")
async def gen_report(body: ReportBody):
    async with core.lock:
        rep = await reports.generate(body.week_offset, project_id=body.project_id)
    bus.publish("data_changed", ["reports"])
    return {"report": serialize.report(rep)}


@router.delete("/reports/{report_id}")
def remove_report(report_id: int):
    with session() as s:
        r = s.get(Report, report_id)
        if r:
            s.delete(r)
    return {"deleted": report_id}


# ---------------------------------------------------------------- memory, goals, follow-ups
@router.get("/memories")
def list_memories():
    with session() as s:
        return {"memories": [serialize.memory(m) for m in s.query(Memory).order_by(Memory.id.desc()).all()]}


@router.post("/memories")
async def create_memory(body: dict[str, Any]):
    return await _tool("remember", body)


@router.delete("/memories/{memory_id}")
async def remove_memory(memory_id: int):
    return await _tool("forget", {"memory_id": memory_id})


@router.get("/goals")
def list_goals():
    with session() as s:
        return {"goals": [serialize.goal(g) for g in s.query(Goal).order_by(Goal.id).all()]}


@router.post("/goals")
async def create_goal(body: dict[str, Any]):
    return await _tool("add_goal", body)


@router.patch("/goals/{goal_id}")
async def patch_goal(goal_id: int, body: dict[str, Any]):
    return await _tool("update_goal", {**body, "goal_id": goal_id})


@router.delete("/goals/{goal_id}")
def remove_goal(goal_id: int):
    with session() as s:
        g = s.get(Goal, goal_id)
        if g:
            s.delete(g)
    bus.publish("data_changed", ["goals"])
    return {"deleted": goal_id}


@router.get("/followups")
def list_followups():
    with session() as s:
        rows = s.query(Followup).filter(Followup.status == "pending").order_by(Followup.due_at).limit(50).all()
        return {"followups": [serialize.followup(f) for f in rows]}


@router.post("/followups/{followup_id}/snooze")
def snooze_followup(followup_id: int, minutes: int = 60):
    from .. import followups as fu

    with session() as s:
        f = fu.snooze(s, followup_id, minutes)
        at = f.due_at
    bus.publish("data_changed", ["followups"])
    return {"due": timeutil.iso(at)}


@router.delete("/followups/{followup_id}")
async def remove_followup(followup_id: int):
    return await _tool("cancel_followup", {"followup_id": followup_id})


# ---------------------------------------------------------------- settings
@router.get("/settings")
def get_settings_api():
    st = get_settings()
    llm = get_llm()
    model = getattr(llm, "model", None)
    return {
        "prefs": {k: v for k, v in prefs.all_prefs().items()},
        "telegram": telegram.status(),
        "llm": {
            "configured": bool(st.gapgpt_api_key),
            "base_url": st.gapgpt_base_url,
            "proxy": bool(st.llm_proxy),
            "chat_model": model("chat") if model else st.chat_model,
            "fast_model": model("fast") if model else st.fast_model,
            "vision_model": model("vision") if model else st.vision_model,
            "ielts_model": model("ielts") if model else st.chat_model,
            "transcribe_model": st.transcribe_model,
            "tts_model": st.tts_model,
            "embedding_model": st.embedding_model,
            "fallback_models": st.fallback_models,
            "env_defaults": {"chat": st.chat_model, "fast": st.fast_model, "vision": st.vision_model,
                             "ielts": st.ielts_model or st.chat_model},
        },
        "budget": llm.budget() if hasattr(llm, "budget") else None,
        "push": {"subscriptions": push.count()},
        "timezone": st.timezone,
    }


@router.patch("/settings")
async def patch_settings(body: dict[str, Any]):
    res = await _tool("update_preferences", {"changes": body})
    return res


@router.post("/telegram/unlink")
def telegram_unlink():
    prefs.set("telegram_chat_id", None)
    prefs.set("telegram_user", None)
    prefs.set("pairing_code", None)
    bus.publish("data_changed", ["settings"])
    return telegram.status()


@router.post("/telegram/test")
async def telegram_test():
    if telegram.owner_chat_id() is None:
        raise HTTPException(400, "تلگرام وصل نیست")
    sent = await outbox.send("text", {"text": "🔔 پیام آزمایشی از پیگیر — تلگرام درست کار می‌کند."})
    if not sent:
        raise HTTPException(502, "ارسال به تلگرام ناموفق بود؛ در صف ارسال مجدد قرار گرفت")
    return {"ok": True}


@router.get("/llm/models")
async def llm_models():
    try:
        return {"models": await get_llm().list_models()}
    except LLMError as e:
        raise HTTPException(502, str(e)) from e


@router.post("/llm/test")
async def llm_test():
    try:
        text = await get_llm().complete("Reply with one short Persian sentence.", "سلام! تست اتصال.")
        return {"ok": True, "reply": text}
    except LLMError as e:
        raise HTTPException(502, str(e)) from e


@router.post("/followups/run")
async def run_followups_now():
    from ..agent import proactive

    return {"sent": await proactive.tick()}
