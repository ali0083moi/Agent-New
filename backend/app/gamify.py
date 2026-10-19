"""Points, levels and a weekly score — computed from your real data, never stored separately."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import habits, prefs, timeutil
from .db import Commitment, HabitLog, IeltsScore, PlannedBlock, Task, VocabWord, WorkLog

LEVELS = [
    "تازه‌کار", "شروع‌کننده", "پیگیر", "منظم", "پرتلاش", "متمرکز", "حرفه‌ای", "قهرمان عادت", "استاد زمان", "افسانه",
]


def points_between(s: Session, start: date, end: date) -> dict[str, int]:
    a, b = timeutil.at(start, "00:00"), timeutil.at(end + timedelta(days=1), "00:00")
    minutes = (
        s.query(func.coalesce(func.sum(WorkLog.minutes), 0))
        .filter(WorkLog.start_at >= a, WorkLog.start_at < b, WorkLog.productive.is_(True), WorkLog.end_at.is_not(None))
        .scalar()
        or 0
    )
    done = s.query(Task).filter(Task.status == "done", Task.completed_at >= a, Task.completed_at < b).all()
    task_pts = 0
    for t in done:
        task_pts += 10 + (5 if t.priority == 1 else 0) + (5 if t.due_at and t.completed_at <= t.due_at else 0)
    blocks = s.query(PlannedBlock).filter(PlannedBlock.status == "done", PlannedBlock.start_at >= a,
                                          PlannedBlock.start_at < b).count()
    commits = s.query(Commitment).filter(Commitment.day >= start, Commitment.day <= end).all()
    commit_pts = sum(15 if c.status == "done" else 5 if c.status == "partial" else 0 for c in commits)
    met, _ = habits.habit_days_met(s, start, end)
    ielts = s.query(IeltsScore).filter(IeltsScore.day >= start, IeltsScore.day <= end).count()
    vocab = (
        s.query(func.coalesce(func.sum(VocabWord.correct), 0)).filter(VocabWord.last_reviewed >= start,
                                                                      VocabWord.last_reviewed <= end).scalar()
        or 0
    )
    parts = {
        "focus": int(minutes) // 10,
        "tasks": task_pts,
        "blocks": blocks * 3,
        "commitments": commit_pts,
        "habits": met * 5,
        "ielts": ielts * 10,
        "vocab": min(int(vocab), 50),
    }
    parts["total"] = sum(parts.values())
    return parts


def level_for(points: int) -> dict[str, Any]:
    # level n starts at 100 * n(n-1)/2 points: 0, 100, 300, 600, 1000...
    n = int((1 + math.sqrt(1 + 8 * points / 100)) / 2)
    start = 100 * n * (n - 1) // 2
    nxt = 100 * (n + 1) * n // 2
    return {
        "level": n,
        "title": LEVELS[min(n - 1, len(LEVELS) - 1)],
        "points": points,
        "level_start": start,
        "next_level_at": nxt,
        "progress": round((points - start) / (nxt - start), 3),
    }


def weekly_score(s: Session, week_start: date) -> dict[str, Any]:
    """0-100 score for a Sat-Fri week; components without data don't count."""
    today = timeutil.today()
    end = min(week_start + timedelta(days=6), today)
    days = (end - week_start).days + 1
    goal = int(prefs.get("daily_focus_goal_minutes", s=s) or 240)
    a, b = timeutil.at(week_start, "00:00"), timeutil.at(end + timedelta(days=1), "00:00")
    minutes = (
        s.query(func.coalesce(func.sum(WorkLog.minutes), 0))
        .filter(WorkLog.start_at >= a, WorkLog.start_at < b, WorkLog.productive.is_(True), WorkLog.end_at.is_not(None))
        .scalar()
        or 0
    )
    comps: dict[str, tuple[float, float]] = {}  # name -> (value 0..1, weight)
    comps["focus"] = (min(1.0, minutes / max(goal * max(days, 1), 1)), 40)
    due = s.query(Task).filter(Task.due_at >= a, Task.due_at < b, Task.status != "cancelled").all()
    if due:
        on_time = sum(1 for t in due if t.status == "done" and t.completed_at and t.completed_at <= t.due_at)
        comps["deadlines"] = (on_time / len(due), 20)
    commits = s.query(Commitment).filter(Commitment.day >= week_start, Commitment.day <= end).all()
    if commits:
        kept = sum(1 if c.status == "done" else 0.5 if c.status == "partial" else 0 for c in commits)
        comps["commitments"] = (kept / len(commits), 20)
    met, expected = habits.habit_days_met(s, week_start, end)
    if expected:
        comps["habits"] = (met / expected, 20)
    total_w = sum(w for _, w in comps.values())
    score = round(100 * sum(v * w for v, w in comps.values()) / total_w) if total_w else 0
    return {
        "score": score,
        "components": {k: round(v * 100) for k, (v, _) in comps.items()},
        "week_start": week_start.isoformat(),
    }


def summary(s: Session) -> dict[str, Any]:
    today = timeutil.today()
    first_log = s.query(func.min(WorkLog.start_at)).scalar()
    first_habit = s.query(func.min(HabitLog.day)).scalar()
    candidates = [d.date() if hasattr(d, "date") else d for d in (first_log, first_habit) if d]
    start = min(candidates) if candidates else today
    total = points_between(s, start, today)["total"]
    ws = timeutil.week_start(today)
    week = points_between(s, ws, today)
    return {
        **level_for(total),
        "week_points": week["total"],
        "week_breakdown": week,
        "weekly_score": weekly_score(s, ws),
        "last_week_score": weekly_score(s, ws - timedelta(days=7)),
    }
