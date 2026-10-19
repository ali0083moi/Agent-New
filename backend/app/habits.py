"""Habits (exercise, reading, sleep...) with streaks, and daily commitments."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import planner, timeutil
from .db import Commitment, Habit, HabitLog, Task, WorkLog

UNIT_FA = {"times": "بار", "minutes": "دقیقه", "hours": "ساعت"}


# ---------------------------------------------------------------- habits
def _day_totals(s: Session, habit_id: int, since: date) -> dict[date, float]:
    rows = (
        s.query(HabitLog.day, func.sum(HabitLog.value))
        .filter(HabitLog.habit_id == habit_id, HabitLog.day >= since)
        .group_by(HabitLog.day)
        .all()
    )
    return {d: float(v or 0) for d, v in rows}


def habit_stats(s: Session, h: Habit, today: date | None = None) -> dict[str, Any]:
    today = today or timeutil.today()
    totals = _day_totals(s, h.id, today - timedelta(days=400))
    last7 = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        v = totals.get(d, 0.0)
        last7.append({"date": d.isoformat(), "value": v, "met": v >= h.target if h.period == "daily" else v > 0})

    if h.period == "daily":
        def met(d: date) -> bool:
            return totals.get(d, 0) >= h.target

        cur = 0
        d = today if met(today) else today - timedelta(days=1)
        while met(d):
            cur += 1
            d -= timedelta(days=1)
        best, run = 0, 0
        d = today - timedelta(days=399)
        while d <= today:
            run = run + 1 if met(d) else 0
            best = max(best, run)
            d += timedelta(days=1)
        done_today = totals.get(today, 0)
        period_total = done_today
    else:
        def week_total(ws: date) -> float:
            return sum(totals.get(ws + timedelta(days=i), 0) for i in range(7))

        ws = timeutil.week_start(today)
        cur = 0
        w = ws if week_total(ws) >= h.target else ws - timedelta(days=7)
        while week_total(w) >= h.target:
            cur += 1
            w -= timedelta(days=7)
        best, run = 0, 0
        w = ws - timedelta(days=7 * 52)
        while w <= ws:
            run = run + 1 if week_total(w) >= h.target else 0
            best = max(best, run)
            w += timedelta(days=7)
        done_today = totals.get(today, 0)
        period_total = week_total(ws)
    return {
        "id": h.id,
        "title": h.title,
        "unit": h.unit,
        "unit_fa": UNIT_FA.get(h.unit, h.unit),
        "target": h.target,
        "period": h.period,
        "reminder_time": h.reminder_time,
        "category": h.category,
        "active": h.active,
        "streak": cur,
        "best_streak": best,
        "today": done_today,
        "period_total": period_total,
        "met_now": period_total >= h.target,
        "last7": last7,
    }


def all_habits(s: Session, active_only: bool = True) -> list[dict[str, Any]]:
    q = s.query(Habit)
    if active_only:
        q = q.filter(Habit.active.is_(True))
    return [habit_stats(s, h) for h in q.order_by(Habit.id).all()]


def habit_days_met(s: Session, start: date, end: date) -> tuple[int, int]:
    """(met, expected) habit-days in a range, for scoring."""
    met = expected = 0
    for h in s.query(Habit).filter(Habit.active.is_(True)).all():
        totals = _day_totals(s, h.id, start)
        if h.period == "daily":
            d = max(start, h.created_at.date())
            while d <= end:
                expected += 1
                met += 1 if totals.get(d, 0) >= h.target else 0
                d += timedelta(days=1)
        else:
            expected += 1
            met += 1 if sum(v for d, v in totals.items() if start <= d <= end) >= h.target else 0
    return met, expected


# ---------------------------------------------------------------- commitments
def commitment_progress(s: Session, c: Commitment) -> dict[str, Any]:
    logged = None
    if c.target_minutes:
        start, end = timeutil.at(c.day, "00:00"), timeutil.at(c.day + timedelta(days=1), "00:00")
        q = s.query(func.coalesce(func.sum(WorkLog.minutes), 0)).filter(
            WorkLog.start_at >= start, WorkLog.start_at < end, WorkLog.productive.is_(True)
        )
        if c.task_id:
            q = q.filter(WorkLog.task_id == c.task_id)
        elif c.category:
            q = q.filter(WorkLog.category == c.category)
        logged = int(q.scalar() or 0)
    task = s.get(Task, c.task_id) if c.task_id else None
    return {
        "id": c.id,
        "day": c.day.isoformat(),
        "text": c.text,
        "target_minutes": c.target_minutes,
        "logged_minutes": logged,
        "task_id": c.task_id,
        "task_done": bool(task and task.status == "done"),
        "category": c.category,
        "status": c.status,
        "note": c.note,
    }


def auto_evaluate(s: Session, day: date) -> list[dict[str, Any]]:
    """Mark commitments met by the data as done; return all of the day's commitments."""
    out = []
    for c in s.query(Commitment).filter(Commitment.day == day).order_by(Commitment.id).all():
        info = commitment_progress(s, c)
        if c.status == "pending":
            if (info["target_minutes"] and (info["logged_minutes"] or 0) >= info["target_minutes"]) or info["task_done"]:
                c.status = "done"
                info["status"] = "done"
        out.append(info)
    return out


def close_past(s: Session, today: date) -> int:
    """Commitments of earlier days still pending become partial (some time logged) or missed."""
    n = 0
    for c in s.query(Commitment).filter(Commitment.day < today, Commitment.status == "pending").all():
        info = commitment_progress(s, c)
        if info["task_done"] or (info["target_minutes"] and (info["logged_minutes"] or 0) >= info["target_minutes"]):
            c.status = "done"
        elif info["logged_minutes"]:
            c.status = "partial"
        else:
            c.status = "missed"
        n += 1
    return n


def remaining_for_task(s: Session, task_id: int) -> int:
    t = s.get(Task, task_id)
    return planner.remaining_minutes(s, t) if t else 0
