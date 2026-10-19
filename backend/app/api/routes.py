"""REST + SSE API for the web UI."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import cal, notify, planner, prefs, reports, serialize, stats, telegram, timeutil
from ..agent import core, tools
from ..bus import bus
from ..config import get_settings
from ..db import Followup, Goal, Memory, Message, RecurringEvent, Report, Task, session
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
    out: dict[str, Any] = {"auth_required": auth.auth_required(), "authenticated": ok, "now": timeutil.iso(timeutil.now())}
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
    ctx = tools.Ctx(channel="web")
    res = await tools.execute(name, args, ctx)
    if "error" in res:
        raise HTTPException(400, res["error"])
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
        kind = "image" if mime.startswith("image/") else "audio" if mime.startswith(("audio/", "video/webm")) else None
        if kind is None:
            raise HTTPException(400, f"نوع فایل پشتیبانی نمی‌شود: {mime}")
        attachments.append(core.Attachment(kind, data, mime, f.filename or ""))
    if not text.strip() and not attachments:
        raise HTTPException(400, "پیام خالی است")
    user_msg, reply = await core.handle_user_message(text, "web", attachments)
    background.add_task(notify.mirror_web_turn, user_msg["content"] or "📎", reply["content"])
    return {"user": user_msg, "assistant": reply}


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
        }


@router.get("/agenda")
def agenda(start: str | None = None, days: int = 7):
    with session() as s:
        d = timeutil.parse_date(start) if start else timeutil.week_start(timeutil.today())
        return {"days": cal.agenda(s, d, max(1, min(days, 31)))}


# ---------------------------------------------------------------- tasks
@router.get("/tasks")
def list_tasks(status: str = "open", category: str | None = None):
    with session() as s:
        q = s.query(Task)
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


# ---------------------------------------------------------------- reports
@router.get("/reports")
def list_reports():
    with session() as s:
        return {"reports": [serialize.report(r) for r in s.query(Report).order_by(Report.week_start.desc()).all()]}


class ReportBody(BaseModel):
    week_offset: int = 0


@router.post("/reports/generate")
async def gen_report(body: ReportBody):
    async with core.lock:
        rep = await reports.generate(body.week_offset)
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


@router.delete("/followups/{followup_id}")
async def remove_followup(followup_id: int):
    return await _tool("cancel_followup", {"followup_id": followup_id})


# ---------------------------------------------------------------- settings
@router.get("/settings")
def get_settings_api():
    st = get_settings()
    return {
        "prefs": prefs.all_prefs(),
        "telegram": telegram.status(),
        "llm": {
            "configured": bool(st.gapgpt_api_key),
            "base_url": st.gapgpt_base_url,
            "chat_model": st.chat_model,
            "fast_model": st.fast_model,
            "vision_model": st.vision_model,
            "transcribe_model": st.transcribe_model,
        },
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
    sent = await telegram.send_to_owner("🔔 پیام آزمایشی از پیگیر — تلگرام درست کار می‌کند.")
    if not sent:
        raise HTTPException(400, "تلگرام وصل نیست")
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
