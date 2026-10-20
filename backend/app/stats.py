"""Productivity statistics."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from . import cal, prefs, timeutil
from .db import PlannedBlock, Task, WorkLog


def productivity(s: Session, start_date: date, end_date: date) -> dict[str, Any]:
    """Stats for [start_date, end_date] inclusive."""
    start = timeutil.at(start_date, "00:00")
    end = timeutil.at(end_date + timedelta(days=1), "00:00")
    now = timeutil.now()
    goal = int(prefs.get("daily_focus_goal_minutes", s=s) or 240)

    logs = (
        s.query(WorkLog)
        .filter(WorkLog.start_at >= start, WorkLog.start_at < end)
        .order_by(WorkLog.start_at)
        .all()
    )
    tasks_by_id = {t.id: t for t in s.query(Task).all()}

    per_day: dict[str, int] = defaultdict(int)
    per_cat: dict[str, int] = defaultdict(int)
    per_task: dict[str, int] = defaultdict(int)
    per_project: dict[str, int] = defaultdict(int)
    from .db import Project

    project_names = {p.id: p.name for p in s.query(Project).all()}
    unproductive = 0
    entries = []
    for lg in logs:
        minutes = lg.minutes
        if lg.end_at is None:  # running focus session
            minutes = int((now - lg.start_at).total_seconds() // 60)
        if not lg.productive:
            unproductive += minutes
            continue
        per_day[lg.start_at.date().isoformat()] += minutes
        per_cat[lg.category] += minutes
        title = tasks_by_id[lg.task_id].title if lg.task_id in tasks_by_id else (lg.description or "بدون عنوان")
        per_task[title] += minutes
        pid = lg.project_id or (tasks_by_id[lg.task_id].project_id if lg.task_id in tasks_by_id else None)
        if pid in project_names:
            per_project[project_names[pid]] += minutes
        entries.append(
            {
                "date": lg.start_at.date().isoformat(),
                "start": timeutil.iso(lg.start_at),
                "minutes": minutes,
                "category": lg.category,
                "task": title,
                "project": project_names.get(pid) if pid else None,
                "description": lg.description,
            }
        )

    completed = (
        s.query(Task)
        .filter(Task.completed_at.is_not(None), Task.completed_at >= start, Task.completed_at < end)
        .order_by(Task.completed_at)
        .all()
    )
    blocks = s.query(PlannedBlock).filter(PlannedBlock.start_at >= start, PlannedBlock.start_at < end).all()
    past_blocks = [b for b in blocks if b.end_at <= now]
    block_stats = {
        "planned": len(blocks),
        "done": sum(1 for b in past_blocks if b.status == "done"),
        "partial": sum(1 for b in past_blocks if b.status == "partial"),
        "skipped": sum(1 for b in past_blocks if b.status in ("skipped", "planned")),
    }
    classes = cal.class_occurrences(s, start, min(end, now))
    days = []
    d = start_date
    while d <= end_date:
        m = per_day.get(d.isoformat(), 0)
        days.append(
            {
                "date": d.isoformat(),
                "weekday_fa": timeutil.WEEKDAY_FA[timeutil.weekday_key(d)],
                "label": timeutil.fa_date(d, with_weekday=False),
                "minutes": m,
                "goal_minutes": goal,
            }
        )
        d += timedelta(days=1)

    total = sum(per_day.values())
    n_days = max(1, min((end_date - start_date).days + 1, (now.date() - start_date).days + 1))
    return {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "total_productive_minutes": total,
        "unproductive_minutes": unproductive,
        "average_per_day_minutes": round(total / n_days),
        "daily_goal_minutes": goal,
        "goal_achievement_percent": round(100 * total / (goal * n_days)) if goal else None,
        "days": days,
        "by_category": [
            {"category": k, "label": cal.CATEGORY_FA.get(k, k), "minutes": v}
            for k, v in sorted(per_cat.items(), key=lambda x: -x[1])
        ],
        "by_task": [{"task": k, "minutes": v} for k, v in sorted(per_task.items(), key=lambda x: -x[1])][:20],
        "by_project": [{"project": k, "minutes": v} for k, v in sorted(per_project.items(), key=lambda x: -x[1])],
        "completed_tasks": [
            {
                "id": t.id,
                "title": t.title,
                "category": t.category,
                "project": project_names.get(t.project_id) if t.project_id else None,
                "completed_at": timeutil.iso(t.completed_at),
                "notes": t.notes,
            }
            for t in completed
        ],
        "blocks": block_stats,
        "classes_held": [{"title": c.title, "start": timeutil.iso(c.start)} for c in classes],
        "entries": entries[-100:],
    }


def today_summary(s: Session) -> dict[str, Any]:
    d = timeutil.today()
    st = productivity(s, d, d)
    return {
        "productive_minutes": st["total_productive_minutes"],
        "goal_minutes": st["daily_goal_minutes"],
        "completed": len(st["completed_tasks"]),
    }


def active_focus(s: Session) -> WorkLog | None:
    return (
        s.query(WorkLog)
        .filter(WorkLog.end_at.is_(None))
        .order_by(WorkLog.start_at.desc())
        .first()
    )


def focus_to_dict(s: Session, lg: WorkLog | None) -> dict[str, Any] | None:
    if lg is None:
        return None
    t = s.get(Task, lg.task_id) if lg.task_id else None
    return {
        "id": lg.id,
        "task_id": lg.task_id,
        "task": t.title if t else None,
        "description": lg.description,
        "category": lg.category,
        "start": timeutil.iso(lg.start_at),
        "elapsed_minutes": int((timeutil.now() - lg.start_at).total_seconds() // 60),
    }


def close_focus(s: Session, lg: WorkLog, end: datetime | None = None, productive: bool = True) -> int:
    end = end or timeutil.now()
    lg.end_at = end
    lg.minutes = max(0, int((end - lg.start_at).total_seconds() // 60))
    lg.productive = productive
    s.flush()
    return lg.minutes
