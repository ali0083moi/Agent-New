"""ORM -> plain dict helpers shared by the API and the agent tools."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from . import cal, planner, timeutil
from .db import Event, Followup, Goal, Memory, Message, RecurringEvent, Report, Task


def task(s: Session, t: Task, with_time: bool = True) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "category": t.category,
        "status": t.status,
        "priority": t.priority,
        "due": timeutil.iso(t.due_at),
        "due_fa": timeutil.fa_datetime(t.due_at) if t.due_at else None,
        "estimated_minutes": t.estimated_minutes,
        "progress": t.progress,
        "notes": t.notes,
        "recurring_event_id": t.recurring_event_id,
        "goal_id": t.goal_id,
        "parent_id": t.parent_id,
        "energy": t.energy or "normal",
        "created_at": timeutil.iso(t.created_at),
        "completed_at": timeutil.iso(t.completed_at),
    }
    if with_time:
        d["spent_minutes"] = planner.spent_minutes(s, t.id)
        d["remaining_minutes"] = planner.remaining_minutes(s, t)
        if t.estimated_minutes and t.status in ("todo", "doing"):
            adj = planner.adjusted_estimate(s, t)
            if adj != t.estimated_minutes:
                d["adjusted_estimate_minutes"] = adj
        kids = s.query(Task.status).filter(Task.parent_id == t.id).all()
        if kids:
            d["subtasks_total"] = len(kids)
            d["subtasks_done"] = sum(1 for (st,) in kids if st == "done")
    return d


def recurring(r: RecurringEvent) -> dict[str, Any]:
    return {
        "id": r.id,
        "title": r.title,
        "kind": r.kind,
        "kind_fa": cal.KIND_FA.get(r.kind, r.kind),
        "weekdays": r.weekdays,
        "weekdays_fa": [timeutil.WEEKDAY_FA.get(w, w) for w in r.weekdays or []],
        "start_time": r.start_time,
        "end_time": r.end_time,
        "location": r.location,
        "instructor": r.instructor,
        "week_parity": r.week_parity,
        "followup": r.followup,
        "start_date": timeutil.iso(r.start_date),
        "end_date": timeutil.iso(r.end_date),
        "notes": r.notes,
        "goal_id": r.goal_id,
        "active": r.active,
    }


def event(e: Event) -> dict[str, Any]:
    return {
        "id": e.id,
        "title": e.title,
        "kind": e.kind,
        "start": timeutil.iso(e.start_at),
        "end": timeutil.iso(e.end_at),
        "location": e.location,
        "notes": e.notes,
    }


def goal(g: Goal) -> dict[str, Any]:
    return {
        "id": g.id,
        "title": g.title,
        "description": g.description,
        "category": g.category,
        "target_date": timeutil.iso(g.target_date),
        "status": g.status,
        "progress_note": g.progress_note,
    }


def memory(m: Memory) -> dict[str, Any]:
    return {"id": m.id, "content": m.content, "category": m.category, "created_at": timeutil.iso(m.created_at)}


def followup(f: Followup) -> dict[str, Any]:
    return {
        "id": f.id,
        "kind": f.kind,
        "due": timeutil.iso(f.due_at),
        "status": f.status,
        "note": f.note,
        "task_id": f.task_id,
        "recurring_event_id": f.recurring_event_id,
        "created_by": f.created_by,
    }


def message(m: Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "channel": m.channel,
        "kind": m.kind,
        "meta": m.meta or {},
        "created_at": m.created_at.isoformat(timespec="seconds"),
    }


def report(r: Report) -> dict[str, Any]:
    return {
        "id": r.id,
        "week_start": r.week_start.isoformat(),
        "week_end": r.week_end.isoformat(),
        "label": f"{timeutil.fa_date(r.week_start, with_weekday=False)} تا {timeutil.fa_date(r.week_end, with_weekday=False, with_year=True)}",
        "work_report": r.work_report,
        "personal_report": r.personal_report,
        "stats": r.stats,
        "created_at": timeutil.iso(r.created_at),
    }
