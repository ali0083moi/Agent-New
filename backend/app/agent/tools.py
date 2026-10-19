"""Function-calling tools the assistant can use."""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from .. import cal, followups, journal, planner, prefs, serialize, stats, timeutil
from ..db import Event, Followup, Goal, Memory, PlannedBlock, RecurringEvent, Task, WorkLog, session

log = logging.getLogger(__name__)

CATEGORIES = ["work", "university", "language", "personal", "health", "other"]
REC_KINDS = list(cal.KIND_CATEGORY)
EVENT_KINDS = ["appointment", "outing", "exam", "meeting", "deadline", "other"]
DT_HELP = "Local datetime 'YYYY-MM-DD HH:MM'. Gregorian, or Jalali if year<1700 (e.g. '1405-08-03 18:00')."


@dataclass
class Ctx:
    channel: str = "web"
    changed: set[str] = field(default_factory=set)
    turn_id: str | None = None  # groups this turn's changes for undo
    trusted: bool = False  # explicit action in the web UI (skips the delete confirmation)
    replan: bool = False


# tools after which the plan doesn't need to be refreshed
NO_REPLAN = {"plan_schedule", "start_focus"}


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]
    changes: tuple[str, ...] = ()

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None,
         changes: tuple[str, ...] = ()):
    def deco(fn):
        REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            parameters={"type": "object", "properties": properties, "required": required or []},
            fn=fn,
            changes=changes,
        )
        return fn

    return deco


def schemas() -> list[dict[str, Any]]:
    return [t.schema() for t in REGISTRY.values()]


async def execute(name: str, raw_args: str | dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    spec = REGISTRY.get(name)
    if spec is None:
        return {"error": f"unknown tool {name}"}
    try:
        args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
    except Exception as e:  # noqa: BLE001
        return {"error": f"invalid JSON arguments: {e}"}
    args = {k: v for k, v in args.items() if v is not None}
    try:
        if inspect.iscoroutinefunction(spec.fn):
            result = await spec.fn(ctx=ctx, **args)
        else:
            with session() as s:
                result = spec.fn(s, ctx=ctx, **args)
        if isinstance(result, dict) and result.get("needs_confirmation"):
            return result
        ctx.changed.update(spec.changes)
        if "tasks" in spec.changes and name not in NO_REPLAN:
            ctx.replan = True
        return result if isinstance(result, dict) else {"result": result}
    except TypeError as e:
        log.warning("tool %s bad args: %s", name, e)
        return {"error": f"bad arguments: {e}"}
    except Exception as e:  # noqa: BLE001
        log.exception("tool %s failed", name)
        return {"error": str(e)}


def finalize(ctx: Ctx) -> None:
    """End of a turn / API call: refresh the plan once if tasks changed."""
    if not ctx.replan:
        return
    ctx.replan = False
    try:
        with session() as s:
            if prefs.get("auto_replan", True, s=s) and planner.open_tasks(s):
                planner.plan(s, days=7)
                ctx.changed.update({"schedule", "followups"})
    except Exception:  # noqa: BLE001
        log.exception("auto re-plan failed")


# ------------------------------------------------------------------ helpers
def _get(s: Session, model, obj_id: int, label: str):
    obj = s.get(model, int(obj_id))
    if obj is None:
        raise ValueError(f"{label} با شناسه {obj_id} پیدا نشد")
    return obj


def _cat(value: str | None, default: str = "personal") -> str:
    if not value:
        return default
    v = value.strip().lower()
    return v if v in CATEGORIES else default


def _parse_end(start: datetime, end: str | None, duration: int | None, default: int = 60) -> datetime:
    """End of a window given as full datetime, bare 'HH:MM' (same day as start) or a duration."""
    if end and len(timeutil.to_latin_digits(str(end)).strip()) <= 5:
        e = timeutil.at(start.date(), end)
        if e <= start:
            e += timedelta(days=1)
        return e
    if end:
        return timeutil.parse_datetime(end)
    return start + timedelta(minutes=int(duration or default))


def _cancel_task_followups(s: Session, task_id: int) -> None:
    s.query(Followup).filter(Followup.task_id == task_id, Followup.status == "pending").update(
        {"status": "cancelled"}
    )


# ------------------------------------------------------------------ agenda
@tool(
    "get_agenda",
    "Get the calendar (classes, events, planned work blocks, deadlines) for one or more days.",
    {
        "start_date": {"type": "string", "description": "YYYY-MM-DD (Gregorian or Jalali). Default today."},
        "days": {"type": "integer", "description": "Number of days, default 1, max 14."},
    },
)
def get_agenda(s: Session, ctx: Ctx, start_date: str | None = None, days: int = 1):
    d = timeutil.parse_date(start_date) if start_date else timeutil.today()
    return {
        "days": cal.agenda(s, d, max(1, min(int(days), 14))),
        "active_focus": stats.focus_to_dict(s, stats.active_focus(s)),
    }


# ------------------------------------------------------------------ tasks
@tool(
    "list_tasks",
    "List tasks.",
    {
        "status": {"type": "string", "enum": ["open", "done", "all"], "description": "default open"},
        "category": {"type": "string", "enum": CATEGORIES},
        "limit": {"type": "integer"},
    },
)
def list_tasks(s: Session, ctx: Ctx, status: str = "open", category: str | None = None, limit: int = 50):
    q = s.query(Task)
    if status == "open":
        q = q.filter(Task.status.in_(["todo", "doing"]))
    elif status == "done":
        q = q.filter(Task.status == "done")
    if category:
        q = q.filter(Task.category == category)
    rows = q.order_by(Task.due_at.is_(None), Task.due_at, Task.priority).limit(int(limit)).all()
    return {"tasks": [serialize.task(s, t) for t in rows]}


@tool(
    "add_task",
    "Create a task / homework / to-do. Use whenever the user mentions something they need to do. "
    "Estimate estimated_minutes yourself if the user did not say. Follow-up reminders are scheduled automatically.",
    {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
        "due": {"type": "string", "description": "Deadline. " + DT_HELP},
        "due_next_session_of": {
            "type": "integer",
            "description": "Recurring class id: deadline becomes the start of that class's next session (homework).",
        },
        "estimated_minutes": {"type": "integer"},
        "priority": {"type": "integer", "enum": [1, 2, 3], "description": "1 high, 2 normal, 3 low"},
        "recurring_event_id": {"type": "integer", "description": "Related class id, if any."},
        "goal_id": {"type": "integer"},
        "energy": {"type": "string", "enum": ["high", "normal", "low"],
                   "description": "high = deep/hard work (essays, projects, studying), low = easy admin/errands"},
        "parent_id": {"type": "integer", "description": "Parent task (makes this a subtask / milestone)."},
    },
    ["title"],
    changes=("tasks", "followups"),
)
def add_task(
    s: Session,
    ctx: Ctx,
    title: str,
    description: str = "",
    category: str | None = None,
    due: str | None = None,
    due_next_session_of: int | None = None,
    estimated_minutes: int | None = None,
    priority: int = 2,
    recurring_event_id: int | None = None,
    goal_id: int | None = None,
    energy: str = "normal",
    parent_id: int | None = None,
):
    due_at = timeutil.parse_datetime(due) if due else None
    parent = _get(s, Task, parent_id, "تسک والد") if parent_id else None
    if parent is not None:
        category = category or parent.category
        recurring_event_id = recurring_event_id or parent.recurring_event_id
        goal_id = goal_id or parent.goal_id
    rec = None
    if due_next_session_of:
        rec = _get(s, RecurringEvent, due_next_session_of, "کلاس")
        nxt = cal.next_occurrence(s, rec, timeutil.now())
        if nxt:
            due_at = nxt.start
        recurring_event_id = rec.id
    elif recurring_event_id:
        rec = _get(s, RecurringEvent, recurring_event_id, "کلاس")
    if rec is not None and not category:
        category = cal.KIND_CATEGORY.get(rec.kind, "personal")
    if rec is not None and not goal_id:
        goal_id = rec.goal_id
    t = Task(
        title=title.strip(),
        description=description or "",
        category=_cat(category),
        due_at=due_at,
        estimated_minutes=estimated_minutes,
        priority=int(priority or 2),
        recurring_event_id=recurring_event_id,
        goal_id=goal_id,
        energy=energy if energy in ("high", "normal", "low") else "normal",
        parent_id=parent.id if parent else None,
        source=ctx.channel,
    )
    s.add(t)
    s.flush()
    journal.created(s, ctx, t, f"تسک «{t.title}»")
    planner.invalidate(s)
    planned = followups.ensure_task_followups(s, t, prefs.all_prefs(s), timeutil.now())
    return {
        "task": serialize.task(s, t),
        "reminders_scheduled": [timeutil.iso(f.due_at) for f in planned if f.kind != "overdue"],
    }


@tool(
    "update_task",
    "Update a task (any field). status: todo|doing|done|cancelled. add_note appends a progress note.",
    {
        "task_id": {"type": "integer"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
        "due": {"type": "string", "description": DT_HELP + " Use 'none' to clear."},
        "estimated_minutes": {"type": "integer"},
        "priority": {"type": "integer", "enum": [1, 2, 3]},
        "status": {"type": "string", "enum": ["todo", "doing", "done", "cancelled"]},
        "progress": {"type": "integer", "description": "percent 0-100"},
        "add_note": {"type": "string"},
        "energy": {"type": "string", "enum": ["high", "normal", "low"]},
    },
    ["task_id"],
    changes=("tasks", "followups", "schedule"),
)
def update_task(s: Session, ctx: Ctx, task_id: int, **fields):
    t = _get(s, Task, task_id, "تسک")
    journal.before_update(s, ctx, t, f"ویرایش «{t.title}»")
    for key in ("title", "description", "estimated_minutes", "priority", "energy"):
        if key in fields:
            setattr(t, key, fields[key])
    if "category" in fields:
        t.category = _cat(fields["category"], t.category)
    if "due" in fields:
        t.due_at = None if str(fields["due"]).lower() in ("none", "null", "") else timeutil.parse_datetime(fields["due"])
    if "progress" in fields:
        t.progress = max(0, min(100, int(fields["progress"])))
    if fields.get("add_note"):
        stamp = timeutil.fa_datetime(timeutil.now())
        t.notes = (t.notes + "\n" if t.notes else "") + f"[{stamp}] {fields['add_note']}"
    if "status" in fields:
        _set_status(s, t, fields["status"], ctx)
    s.flush()
    if t.status in ("todo", "doing"):
        followups.ensure_task_followups(s, t, prefs.all_prefs(s), timeutil.now())
    return {"task": serialize.task(s, t)}


def _set_status(s: Session, t: Task, status: str, ctx: Ctx | None = None) -> None:
    t.status = status
    if status == "done":
        t.completed_at = timeutil.now()
        t.progress = 100
    if status in ("done", "cancelled"):
        _cancel_task_followups(s, t.id)
        s.query(PlannedBlock).filter(
            PlannedBlock.task_id == t.id, PlannedBlock.start_at > timeutil.now(), PlannedBlock.status == "planned"
        ).delete()
    else:
        t.completed_at = None
    s.flush()
    planner.invalidate(s)
    if t.parent_id:
        _sync_parent(s, t.parent_id, ctx)


def _sync_parent(s: Session, parent_id: int, ctx: Ctx | None) -> None:
    """A project's progress follows its subtasks; it completes when all of them are done."""
    parent = s.get(Task, parent_id)
    if parent is None:
        return
    kids = s.query(Task).filter(Task.parent_id == parent.id, Task.status != "cancelled").all()
    if not kids:
        return
    done = sum(1 for k in kids if k.status == "done")
    if ctx is not None:
        journal.before_update(s, ctx, parent, f"پیشرفت «{parent.title}»")
    parent.progress = round(100 * done / len(kids))
    if done == len(kids) and parent.status != "done":
        _set_status(s, parent, "done", ctx)
    elif done < len(kids) and parent.status == "done":
        parent.status, parent.completed_at = "doing", None


@tool(
    "complete_task",
    "Mark a task done. Optionally log how many minutes it took.",
    {
        "task_id": {"type": "integer"},
        "spent_minutes": {"type": "integer", "description": "Time spent now (not already logged)."},
        "note": {"type": "string"},
    },
    ["task_id"],
    changes=("tasks", "followups", "stats", "schedule"),
)
def complete_task(s: Session, ctx: Ctx, task_id: int, spent_minutes: int | None = None, note: str | None = None):
    t = _get(s, Task, task_id, "تسک")
    journal.before_update(s, ctx, t, f"انجام «{t.title}»")
    if spent_minutes:
        end = timeutil.now()
        lg = WorkLog(
            task_id=t.id,
            description=note or t.title,
            category=t.category,
            start_at=end - timedelta(minutes=int(spent_minutes)),
            end_at=end,
            minutes=int(spent_minutes),
            source="chat",
        )
        s.add(lg)
        journal.created(s, ctx, lg)
    if note:
        t.notes = (t.notes + "\n" if t.notes else "") + note
    _set_status(s, t, "done", ctx)
    s.flush()
    return {"task": serialize.task(s, t)}


_CONFIRM = {"type": "boolean", "description": "true only after the user confirmed in a previous message"}


@tool("delete_task", "Delete a task permanently (asks for confirmation first). Prefer status=cancelled.",
      {"task_id": {"type": "integer"}, "confirmed": _CONFIRM}, ["task_id"], changes=("tasks", "followups", "schedule"))
def delete_task(s: Session, ctx: Ctx, task_id: int, confirmed: bool = False):
    t = _get(s, Task, task_id, "تسک")
    guard = journal.confirm_guard(ctx, f"task:{t.id}", f"تسک «{t.title}»", confirmed)
    if guard:
        return guard
    for child in s.query(Task).filter(Task.parent_id == t.id).all():
        journal.before_delete(s, ctx, child)
    journal.before_delete(s, ctx, t, f"حذف «{t.title}»")
    s.delete(t)
    return {"deleted": task_id}


# ------------------------------------------------------------------ recurring classes
_REC_PROPS = {
    "title": {"type": "string"},
    "kind": {"type": "string", "enum": REC_KINDS},
    "weekdays": {"type": "array", "items": {"type": "string", "enum": timeutil.WEEK_ORDER}},
    "start_time": {"type": "string", "description": "HH:MM"},
    "end_time": {"type": "string", "description": "HH:MM"},
    "location": {"type": "string"},
    "instructor": {"type": "string"},
    "week_parity": {
        "type": "string",
        "enum": ["all", "odd", "even"],
        "description": "all = every week; odd = هفته‌های فرد; even = هفته‌های زوج",
    },
    "followup": {"type": "boolean", "description": "Ask about homework after each session (default true)."},
    "start_date": {"type": "string", "description": "YYYY-MM-DD, optional"},
    "end_date": {"type": "string", "description": "YYYY-MM-DD, optional (end of semester)"},
    "notes": {"type": "string"},
    "goal_id": {"type": "integer"},
}


def _apply_rec(r: RecurringEvent, data: dict[str, Any]) -> None:
    for key in ("title", "location", "instructor", "notes", "goal_id", "followup"):
        if key in data:
            setattr(r, key, data[key])
    if "kind" in data:
        r.kind = data["kind"] if data["kind"] in REC_KINDS else "other"
    if "weekdays" in data:
        r.weekdays = [timeutil.normalize_weekday(w) for w in data["weekdays"]]
    if "start_time" in data:
        r.start_time = timeutil.parse_time(data["start_time"]).strftime("%H:%M")
    if "end_time" in data:
        r.end_time = timeutil.parse_time(data["end_time"]).strftime("%H:%M")
    if "week_parity" in data:
        r.week_parity = data["week_parity"] if data["week_parity"] in ("all", "odd", "even") else "all"
    if "start_date" in data:
        r.start_date = timeutil.parse_date(data["start_date"]) if data["start_date"] else None
    if "end_date" in data:
        r.end_date = timeutil.parse_date(data["end_date"]) if data["end_date"] else None


@tool(
    "add_recurring_events",
    "Add one or more weekly recurring classes/commitments (university classes, language class, work shifts). "
    "Use this for timetables extracted from images too (add all classes in one call).",
    {"events": {"type": "array", "items": {"type": "object", "properties": _REC_PROPS,
                                            "required": ["title", "weekdays", "start_time", "end_time"]}}},
    ["events"],
    changes=("schedule", "followups"),
)
def add_recurring_events(s: Session, ctx: Ctx, events: list[dict[str, Any]]):
    created = []
    for data in events:
        data = {k: v for k, v in data.items() if v is not None}
        r = RecurringEvent(title=data.get("title", "کلاس"), start_time="00:00", end_time="00:00")
        r.kind = "university_class"
        r.followup = True
        r.week_parity = "all"
        _apply_rec(r, data)
        if not r.weekdays:
            raise ValueError(f"روزهای هفته برای «{r.title}» مشخص نیست")
        s.add(r)
        s.flush()
        journal.created(s, ctx, r, f"کلاس «{r.title}»")
        created.append(serialize.recurring(r))
    return {"created": created}


@tool(
    "update_recurring_event",
    "Update a recurring class/commitment. Set active=false to pause it.",
    {"recurring_event_id": {"type": "integer"}, **_REC_PROPS, "active": {"type": "boolean"}},
    ["recurring_event_id"],
    changes=("schedule", "followups"),
)
def update_recurring_event(s: Session, ctx: Ctx, recurring_event_id: int, **data):
    r = _get(s, RecurringEvent, recurring_event_id, "کلاس")
    journal.before_update(s, ctx, r, f"ویرایش کلاس «{r.title}»")
    _apply_rec(r, data)
    if "active" in data:
        r.active = bool(data["active"])
    s.flush()
    return {"recurring_event": serialize.recurring(r)}


@tool("delete_recurring_event", "Delete a recurring class/commitment (asks for confirmation first).",
      {"recurring_event_id": {"type": "integer"}, "confirmed": _CONFIRM},
      ["recurring_event_id"], changes=("schedule", "followups"))
def delete_recurring_event(s: Session, ctx: Ctx, recurring_event_id: int, confirmed: bool = False):
    r = _get(s, RecurringEvent, recurring_event_id, "کلاس")
    guard = journal.confirm_guard(ctx, f"recurring:{r.id}", f"کلاس «{r.title}»", confirmed)
    if guard:
        return guard
    journal.before_delete(s, ctx, r, f"حذف کلاس «{r.title}»")
    s.delete(r)
    return {"deleted": recurring_event_id}


@tool("list_recurring_events", "List all weekly recurring classes/commitments.", {})
def list_recurring_events(s: Session, ctx: Ctx):
    return {"recurring_events": [serialize.recurring(r) for r in s.query(RecurringEvent).all()]}


# ------------------------------------------------------------------ one-off events
@tool(
    "add_event",
    "Add a one-off calendar event (appointment, outing, exam, meeting).",
    {
        "title": {"type": "string"},
        "start": {"type": "string", "description": DT_HELP},
        "end": {"type": "string", "description": DT_HELP + " or just HH:MM"},
        "duration_minutes": {"type": "integer"},
        "kind": {"type": "string", "enum": EVENT_KINDS},
        "location": {"type": "string"},
        "notes": {"type": "string"},
    },
    ["title", "start"],
    changes=("schedule",),
)
def add_event(s: Session, ctx: Ctx, title: str, start: str, end: str | None = None, duration_minutes: int | None = None,
              kind: str = "appointment", location: str = "", notes: str = ""):
    st = timeutil.parse_datetime(start)
    en = _parse_end(st, end, duration_minutes)
    ev = Event(title=title, start_at=st, end_at=en, kind=kind if kind in EVENT_KINDS else "other",
               location=location, notes=notes)
    s.add(ev)
    s.flush()
    journal.created(s, ctx, ev, f"رویداد «{ev.title}»")
    clash = [
        {"title": t, "start": timeutil.iso(a), "end": timeutil.iso(b)}
        for a, b, t in cal.busy_intervals(s, st, en, class_buffer=0, exclude_event_ids={ev.id})
        if b > st and a < en
    ]
    return {"event": serialize.event(ev), "conflicts": clash}


@tool(
    "update_event",
    "Update a one-off event.",
    {
        "event_id": {"type": "integer"},
        "title": {"type": "string"},
        "start": {"type": "string", "description": DT_HELP},
        "end": {"type": "string", "description": DT_HELP},
        "location": {"type": "string"},
        "notes": {"type": "string"},
    },
    ["event_id"],
    changes=("schedule",),
)
def update_event(s: Session, ctx: Ctx, event_id: int, **data):
    ev = _get(s, Event, event_id, "رویداد")
    journal.before_update(s, ctx, ev, f"ویرایش «{ev.title}»")
    duration = ev.end_at - ev.start_at
    if "start" in data:
        ev.start_at = timeutil.parse_datetime(data["start"])
        if "end" not in data:
            ev.end_at = ev.start_at + duration
    if "end" in data:
        ev.end_at = _parse_end(ev.start_at, data["end"], None)
    for key in ("title", "location", "notes"):
        if key in data:
            setattr(ev, key, data[key])
    s.flush()
    return {"event": serialize.event(ev)}


@tool("delete_event", "Delete a one-off event (asks for confirmation first).",
      {"event_id": {"type": "integer"}, "confirmed": _CONFIRM}, ["event_id"], changes=("schedule",))
def delete_event(s: Session, ctx: Ctx, event_id: int, confirmed: bool = False):
    ev = _get(s, Event, event_id, "رویداد")
    guard = journal.confirm_guard(ctx, f"event:{ev.id}", f"رویداد «{ev.title}»", confirmed)
    if guard:
        return guard
    journal.before_delete(s, ctx, ev, f"حذف «{ev.title}»")
    s.delete(ev)
    return {"deleted": event_id}


# ------------------------------------------------------------------ planning
@tool(
    "check_availability",
    "Decide if the user can spend a time window on something else (e.g. going out with a friend). "
    "Checks hard conflicts AND whether losing that time makes upcoming deadlines infeasible given the workload. "
    "ALWAYS use this before answering 'can I go out at X?'.",
    {
        "start": {"type": "string", "description": DT_HELP},
        "end": {"type": "string", "description": DT_HELP + " or HH:MM"},
        "duration_minutes": {"type": "integer"},
        "buffer_minutes": {"type": "integer", "description": "Travel/prep time to add on each side (default 30)."},
    },
    ["start"],
)
def check_availability(s: Session, ctx: Ctx, start: str, end: str | None = None, duration_minutes: int | None = None,
                       buffer_minutes: int = 30):
    st = timeutil.parse_datetime(start)
    en = _parse_end(st, end, duration_minutes, default=180)
    return planner.check_availability(s, st, en, int(buffer_minutes))


@tool(
    "plan_schedule",
    "(Re)plan focused work blocks for all open tasks over the next days, by deadline and priority, around "
    "classes and events. Returns the blocks plus warnings for tasks that will not fit before their deadline.",
    {"days": {"type": "integer", "description": "Planning horizon in days (default 7)."}},
    changes=("schedule", "followups"),
)
def plan_schedule(s: Session, ctx: Ctx, days: int = 7):
    res = planner.plan(s, days=max(1, min(int(days), 21)))
    return {"blocks": res.blocks, "unscheduled": res.unscheduled, "warnings": res.warnings}


# ------------------------------------------------------------------ time tracking
@tool(
    "log_work",
    "Record time the user spent working (e.g. 'I studied 2 hours'). Use productive=false for time wasted.",
    {
        "description": {"type": "string"},
        "minutes": {"type": "integer"},
        "start": {"type": "string", "description": DT_HELP + " (optional)"},
        "end": {"type": "string", "description": DT_HELP + " (optional)"},
        "task_id": {"type": "integer"},
        "category": {"type": "string", "enum": CATEGORIES},
        "productive": {"type": "boolean"},
        "block_id": {"type": "integer", "description": "Planned block this work belongs to (marks it)."},
        "block_status": {"type": "string", "enum": ["done", "partial", "skipped"]},
        "progress": {"type": "integer", "description": "New task progress percent (optional)."},
    },
    ["description"],
    changes=("stats", "tasks", "schedule"),
)
def log_work(s: Session, ctx: Ctx, description: str, minutes: int | None = None, start: str | None = None,
             end: str | None = None, task_id: int | None = None, category: str | None = None,
             productive: bool = True, block_id: int | None = None, block_status: str | None = None,
             progress: int | None = None):
    t = _get(s, Task, task_id, "تسک") if task_id else None
    if start:
        st = timeutil.parse_datetime(start)
        en = _parse_end(st, end, minutes)
    else:
        en = timeutil.parse_datetime(end) if end else timeutil.now()
        st = en - timedelta(minutes=int(minutes or 0))
    mins = int((en - st).total_seconds() // 60)
    if mins <= 0 and not block_status:
        raise ValueError("مدت زمان مشخص نیست")
    lg = None
    if mins > 0:
        lg = WorkLog(task_id=t.id if t else None, description=description,
                     category=_cat(category, t.category if t else "personal"),
                     start_at=st, end_at=en, minutes=mins, productive=bool(productive), source="chat")
        s.add(lg)
        journal.created(s, ctx, lg, f"ثبت {timeutil.fa_duration(mins)}")
    if t is not None:
        journal.before_update(s, ctx, t)
        if progress is not None:
            t.progress = max(0, min(100, int(progress)))
        if t.status == "todo":
            t.status = "doing"
    if block_id:
        b = s.get(PlannedBlock, int(block_id))
        if b:
            journal.before_update(s, ctx, b)
            b.status = block_status or "done"
    s.flush()
    return {"logged_minutes": mins, "task": serialize.task(s, t) if t else None,
            "today_total_minutes": planner.logged_minutes(s, timeutil.today())}


@tool(
    "start_focus",
    "Start a focus/work timer (the user says they are starting to work on something).",
    {"task_id": {"type": "integer"}, "description": {"type": "string"},
     "category": {"type": "string", "enum": CATEGORIES}},
    changes=("stats", "tasks"),
)
def start_focus(s: Session, ctx: Ctx, task_id: int | None = None, description: str = "", category: str | None = None):
    closed = None
    cur = stats.active_focus(s)
    if cur is not None:
        journal.before_update(s, ctx, cur)
        closed = stats.close_focus(s, cur)
    t = _get(s, Task, task_id, "تسک") if task_id else None
    if t and t.status == "todo":
        journal.before_update(s, ctx, t)
        t.status = "doing"
    lg = WorkLog(task_id=t.id if t else None, description=description or (t.title if t else "کار"),
                 category=_cat(category, t.category if t else "personal"), start_at=timeutil.now(),
                 end_at=None, source="focus")
    s.add(lg)
    s.flush()
    journal.created(s, ctx, lg, "تایمر تمرکز")
    return {"focus": stats.focus_to_dict(s, lg), "previous_session_closed_minutes": closed}


@tool(
    "stop_focus",
    "Stop the running focus timer and log the time.",
    {"note": {"type": "string"}, "progress": {"type": "integer", "description": "task progress percent"},
     "productive": {"type": "boolean"}, "task_done": {"type": "boolean"}},
    changes=("stats", "tasks", "schedule", "followups"),
)
def stop_focus(s: Session, ctx: Ctx, note: str | None = None, progress: int | None = None, productive: bool = True,
               task_done: bool = False):
    cur = stats.active_focus(s)
    if cur is None:
        return {"error": "تایمر فعالی وجود ندارد"}
    journal.before_update(s, ctx, cur, "توقف تایمر")
    minutes = stats.close_focus(s, cur, productive=productive)
    if note:
        cur.description = f"{cur.description} — {note}" if cur.description else note
    t = s.get(Task, cur.task_id) if cur.task_id else None
    if t is not None:
        journal.before_update(s, ctx, t)
        if progress is not None:
            t.progress = max(0, min(100, int(progress)))
        if task_done:
            _set_status(s, t, "done", ctx)
        # mark overlapping planned block
        for b in s.query(PlannedBlock).filter(PlannedBlock.task_id == t.id, PlannedBlock.status == "planned",
                                              PlannedBlock.start_at < cur.end_at, PlannedBlock.end_at > cur.start_at):
            journal.before_update(s, ctx, b)
            b.status = "done"
    s.flush()
    return {"logged_minutes": minutes, "task": serialize.task(s, t) if t else None,
            "today_total_minutes": planner.logged_minutes(s, timeutil.today())}


@tool(
    "get_stats",
    "Productivity stats: productive minutes per day/category/task, completed tasks, planned blocks done/skipped.",
    {
        "period": {"type": "string", "enum": ["today", "yesterday", "this_week", "last_week", "last_7_days", "this_month"]},
        "start_date": {"type": "string"},
        "end_date": {"type": "string"},
    },
)
def get_stats(s: Session, ctx: Ctx, period: str = "today", start_date: str | None = None, end_date: str | None = None):
    today = timeutil.today()
    if start_date:
        a = timeutil.parse_date(start_date)
        b = timeutil.parse_date(end_date) if end_date else today
    elif period == "yesterday":
        a = b = today - timedelta(days=1)
    elif period == "this_week":
        a, b = timeutil.week_start(today), today
    elif period == "last_week":
        a = timeutil.week_start(today) - timedelta(days=7)
        b = a + timedelta(days=6)
    elif period == "last_7_days":
        a, b = today - timedelta(days=6), today
    elif period == "this_month":
        j = timeutil.jalali(today)
        a = timeutil.parse_date(f"{j.year}-{j.month:02d}-01")
        b = today
    else:
        a = b = today
    st = stats.productivity(s, a, b)
    if (b - a).days > 7:
        st.pop("entries", None)
    return st


# ------------------------------------------------------------------ follow-ups
@tool(
    "schedule_followup",
    "Schedule a custom reminder / check-in message to the user at a specific time "
    "(e.g. 'remind me at 5 to call mom', or to check on something later).",
    {"at": {"type": "string", "description": DT_HELP}, "note": {"type": "string", "description": "What to remind/ask."},
     "task_id": {"type": "integer"}},
    ["at", "note"],
    changes=("followups",),
)
def schedule_followup(s: Session, ctx: Ctx, at: str, note: str, task_id: int | None = None):
    due = timeutil.parse_datetime(at)
    f = Followup(kind="custom", due_at=due, note=note, task_id=task_id, created_by="agent")
    s.add(f)
    s.flush()
    journal.created(s, ctx, f, f"یادآوری «{note[:40]}»")
    return {"followup": serialize.followup(f)}


@tool("list_followups", "List upcoming scheduled reminders/check-ins.", {"limit": {"type": "integer"}})
def list_followups(s: Session, ctx: Ctx, limit: int = 20):
    rows = (
        s.query(Followup).filter(Followup.status == "pending").order_by(Followup.due_at).limit(int(limit)).all()
    )
    return {"followups": [serialize.followup(f) for f in rows]}


@tool("cancel_followup", "Cancel a scheduled reminder/check-in.", {"followup_id": {"type": "integer"}},
      ["followup_id"], changes=("followups",))
def cancel_followup(s: Session, ctx: Ctx, followup_id: int):
    f = _get(s, Followup, followup_id, "یادآوری")
    journal.before_update(s, ctx, f, "لغو یادآوری")
    f.status = "cancelled"
    return {"cancelled": followup_id}


# ------------------------------------------------------------------ memory & goals
@tool(
    "remember",
    "Save a long-term fact about the user (preferences, routines, people, goals context, workplace, etc.).",
    {"content": {"type": "string"},
     "category": {"type": "string", "enum": ["profile", "preference", "routine", "person", "work", "study", "fact"]}},
    ["content"],
    changes=("memories",),
)
def remember(s: Session, ctx: Ctx, content: str, category: str = "fact"):
    m = Memory(content=content.strip(), category=category, source="agent")
    s.add(m)
    s.flush()
    journal.created(s, ctx, m, "حافظه")
    return {"memory": serialize.memory(m)}


@tool("forget", "Delete a saved memory that is wrong or outdated (no confirmation needed when the user asked).",
      {"memory_id": {"type": "integer"}}, ["memory_id"], changes=("memories",))
def forget(s: Session, ctx: Ctx, memory_id: int):
    m = _get(s, Memory, memory_id, "حافظه")
    journal.before_delete(s, ctx, m, "فراموشی")
    s.delete(m)
    return {"deleted": memory_id}


@tool(
    "add_goal",
    "Add a long-term goal (e.g. reach IELTS 7 by a date).",
    {"title": {"type": "string"}, "description": {"type": "string"},
     "category": {"type": "string", "enum": CATEGORIES}, "target_date": {"type": "string"}},
    ["title"],
    changes=("goals",),
)
def add_goal(s: Session, ctx: Ctx, title: str, description: str = "", category: str = "personal",
             target_date: str | None = None):
    g = Goal(title=title, description=description, category=_cat(category),
             target_date=timeutil.parse_date(target_date) if target_date else None)
    s.add(g)
    s.flush()
    journal.created(s, ctx, g, f"هدف «{g.title}»")
    return {"goal": serialize.goal(g)}


@tool(
    "update_goal",
    "Update a goal (status active|achieved|dropped, progress_note...).",
    {"goal_id": {"type": "integer"}, "title": {"type": "string"}, "description": {"type": "string"},
     "target_date": {"type": "string"}, "status": {"type": "string", "enum": ["active", "achieved", "dropped"]},
     "progress_note": {"type": "string"}},
    ["goal_id"],
    changes=("goals",),
)
def update_goal(s: Session, ctx: Ctx, goal_id: int, **data):
    g = _get(s, Goal, goal_id, "هدف")
    journal.before_update(s, ctx, g, f"هدف «{g.title}»")
    for key in ("title", "description", "status", "progress_note"):
        if key in data:
            setattr(g, key, data[key])
    if "target_date" in data:
        g.target_date = timeutil.parse_date(data["target_date"]) if data["target_date"] else None
    s.flush()
    return {"goal": serialize.goal(g)}


# ------------------------------------------------------------------ reports & prefs
@tool(
    "weekly_report",
    "Generate the weekly report (a work report for the employer + a personal review). "
    "week_offset 0 = current week (Sat-Fri), -1 = last week.",
    {"week_offset": {"type": "integer"}},
    changes=("reports",),
)
async def weekly_report(ctx: Ctx, week_offset: int = 0):
    from .. import reports

    rep = await reports.generate(int(week_offset))
    return {"report_id": rep.id, "work_report": rep.work_report, "personal_report": rep.personal_report,
            "note": "The report is saved and visible on the Reports page. Show both parts to the user."}


@tool(
    "update_preferences",
    "Change the user's preferences / routine settings. Keys: user_name, assistant_name, wake_time, sleep_time, "
    "work_start, work_end (productive window HH:MM), daily_brief_time, daily_review_time, weekly_report_day "
    "(sat..fri), weekly_report_time, followup_intensity (low|normal|high), idle_checkin_hours, "
    "post_class_delay_minutes, block_nudges, auto_plan_daily, focus_block_minutes, break_minutes, "
    "max_focus_minutes_per_day, daily_focus_goal_minutes, class_buffer_minutes, default_task_minutes, "
    "semester_start (YYYY-MM-DD), employer_name, work_report_categories (list), mirror_web_to_telegram, "
    "voice_replies (off|when_voice|always), auto_replan, rescue_mode, weekly_planning_day, weekly_planning_time, "
    "ielts_target_band, ielts_exam_date, vocab_review_time, vocab_daily_count, daily_budget_usd, "
    "adaptive_reminders, semantic_memory, auto_facts, llm_chat_model / llm_fast_model / llm_vision_model / "
    "llm_ielts_model (override AI models; '' = default). "
    "Special key current_week_parity ('odd'|'even') tells which university week (فرد/زوج) the current week is.",
    {"changes": {"type": "object", "description": "key -> new value"}},
    ["changes"],
    changes=("settings", "followups", "schedule"),
)
def update_preferences(s: Session, ctx: Ctx, changes: dict[str, Any]):
    applied = {}
    for key, value in changes.items():
        if key == "current_week_parity":
            ws = timeutil.week_start(timeutil.today())
            # week 1 is odd: if the current week is odd, the semester "starts" now, else one week earlier
            start = ws if value == "odd" else ws - timedelta(days=7)
            journal.pref_change(s, ctx, "semester_start", prefs.get("semester_start", s=s))
            prefs.set("semester_start", start.isoformat(), s=s)
            applied["semester_start"] = start.isoformat()
            continue
        if key not in prefs.DEFAULTS:
            raise ValueError(f"تنظیم ناشناخته: {key}")
        if key.endswith("_time") or key in ("work_start", "work_end"):
            value = timeutil.parse_time(value).strftime("%H:%M")
        if key in ("weekly_report_day", "weekly_planning_day"):
            value = timeutil.normalize_weekday(value)
        if key in ("semester_start", "ielts_exam_date") and value:
            value = timeutil.parse_date(value).isoformat()
        journal.pref_change(s, ctx, key, prefs.get(key, s=s))
        prefs.set(key, value, s=s)
        applied[key] = value
    return {"applied": applied}


from . import tools_more  # noqa: E402,F401  (registers the remaining tools)
