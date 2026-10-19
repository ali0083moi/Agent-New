"""What the assistant learns from your data: estimation bias, peak hours, reminder responsiveness."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import cal, prefs, timeutil
from .db import Followup, RecurringEvent, Task, WorkLog

MIN_SAMPLES = 3
FACTOR_RANGE = (0.5, 3.0)


# ---------------------------------------------------------------- estimation bias
def _spent_by_task(s: Session, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    rows = (
        s.query(WorkLog.task_id, func.sum(WorkLog.minutes))
        .filter(WorkLog.task_id.in_(ids), WorkLog.end_at.is_not(None))
        .group_by(WorkLog.task_id)
        .all()
    )
    return {tid: int(m or 0) for tid, m in rows}


def estimation_factors(s: Session, days: int = 90) -> dict[str, Any]:
    """Median actual/estimate ratio per class and per category, from finished tasks with logged time."""
    since = timeutil.now() - timedelta(days=days)
    done = (
        s.query(Task)
        .filter(Task.status == "done", Task.estimated_minutes.is_not(None), Task.estimated_minutes > 0,
                Task.completed_at >= since)
        .all()
    )
    spent = _spent_by_task(s, [t.id for t in done])
    by_cat: dict[str, list[float]] = defaultdict(list)
    by_class: dict[int, list[float]] = defaultdict(list)
    for t in done:
        sp = spent.get(t.id, 0)
        if sp < 10:
            continue
        ratio = sp / t.estimated_minutes
        by_cat[t.category].append(ratio)
        if t.recurring_event_id:
            by_class[t.recurring_event_id].append(ratio)

    def summarize(values: list[float]) -> dict[str, Any]:
        f = min(max(statistics.median(values), FACTOR_RANGE[0]), FACTOR_RANGE[1])
        return {"factor": round(f, 2), "samples": len(values)}

    classes = {r.id: r.title for r in s.query(RecurringEvent).all()}
    return {
        "category": {k: summarize(v) for k, v in by_cat.items() if len(v) >= MIN_SAMPLES},
        "class": {
            k: {**summarize(v), "title": classes.get(k, "")} for k, v in by_class.items() if len(v) >= MIN_SAMPLES
        },
    }


def factor_for(task: Task, factors: dict[str, Any]) -> float:
    if task.recurring_event_id and task.recurring_event_id in factors["class"]:
        return factors["class"][task.recurring_event_id]["factor"]
    if task.category in factors["category"]:
        return factors["category"][task.category]["factor"]
    return 1.0


def estimation_lines(factors: dict[str, Any]) -> list[str]:
    lines = []
    for cid, v in factors["class"].items():
        if abs(v["factor"] - 1) >= 0.15:
            lines.append(f"تکلیف‌های «{v['title']}» معمولاً {v['factor']}× تخمین طول می‌کشند (n={v['samples']})")
    for cat, v in factors["category"].items():
        if abs(v["factor"] - 1) >= 0.15:
            lines.append(f"کارهای «{cal.CATEGORY_FA.get(cat, cat)}» معمولاً {v['factor']}× تخمین طول می‌کشند (n={v['samples']})")
    return lines


# ---------------------------------------------------------------- productive hours
def hour_profile(s: Session, days: int = 42) -> list[int]:
    """Productive minutes per hour of day (0..23), spreading each log over the hours it covers."""
    since = timeutil.now() - timedelta(days=days)
    minutes = [0] * 24
    for lg in s.query(WorkLog).filter(WorkLog.start_at >= since, WorkLog.productive.is_(True),
                                      WorkLog.end_at.is_not(None)).all():
        cur, end = lg.start_at, lg.end_at
        while cur < end:
            nxt = min(end, (cur + timedelta(hours=1)).replace(minute=0, second=0))
            minutes[cur.hour] += int((nxt - cur).total_seconds() // 60)
            cur = nxt
    return minutes


DEFAULT_PEAKS = [9, 10, 11]


def peak_hours(s: Session, profile: list[int] | None = None) -> dict[str, Any]:
    profile = profile if profile is not None else hour_profile(s)
    total = sum(profile)
    if total < 600:  # less than 10 hours of data: assume mornings
        return {"hours": DEFAULT_PEAKS, "learned": False, "profile": profile}
    top = max(profile)
    hours = [h for h, m in enumerate(profile) if m >= 0.6 * top]
    return {"hours": hours, "learned": True, "profile": profile}


def describe_hours(hours: list[int]) -> str:
    if not hours:
        return ""
    hours = sorted(hours)
    ranges: list[tuple[int, int]] = []
    start = prev = hours[0]
    for h in hours[1:]:
        if h == prev + 1:
            prev = h
            continue
        ranges.append((start, prev))
        start = prev = h
    ranges.append((start, prev))
    return "، ".join(f"{a:02d}-{b + 1:02d}" for a, b in ranges)


# ---------------------------------------------------------------- reminder responsiveness
RESPONSE_WINDOW = timedelta(minutes=90)


def mark_responded(s: Session, now: datetime) -> int:
    rows = (
        s.query(Followup)
        .filter(Followup.status == "sent", Followup.responded_at.is_(None), Followup.sent_at >= now - RESPONSE_WINDOW)
        .all()
    )
    for f in rows:
        f.responded_at = now
    return len(rows)


def response_stats(s: Session, days: int = 30) -> dict[str, Any]:
    since = timeutil.now() - timedelta(days=days)
    now = timeutil.now()
    rows = (
        s.query(Followup)
        .filter(Followup.status == "sent", Followup.sent_at >= since, Followup.sent_at <= now - RESPONSE_WINDOW)
        .all()
    )
    by_kind: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_hour: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for f in rows:
        hit = 1 if f.responded_at else 0
        by_kind[f.kind][0] += 1
        by_kind[f.kind][1] += hit
        by_hour[f.sent_at.hour][0] += 1
        by_hour[f.sent_at.hour][1] += hit
    return {
        "kinds": {k: {"sent": v[0], "answered": v[1], "rate": round(v[1] / v[0], 2)} for k, v in by_kind.items()},
        "hours": {h: {"sent": v[0], "answered": v[1], "rate": round(v[1] / v[0], 2)} for h, v in sorted(by_hour.items())},
    }


ADJUSTABLE = {"deadline", "task_checkin", "vocab_review", "habit_reminder"}


def adjust_time(s: Session, at: datetime, kind: str, now: datetime, stats: dict[str, Any] | None = None) -> datetime:
    """Move a flexible reminder out of an hour the user usually ignores (only earlier, never later)."""
    if kind not in ADJUSTABLE or not prefs.get("adaptive_reminders", True):
        return at
    stats = stats or response_stats(s)
    hours = stats["hours"]
    cur = hours.get(at.hour)
    if not cur or cur["sent"] < 4 or cur["rate"] >= 0.3:
        return at
    for back in range(1, 4):
        cand = at - timedelta(hours=back)
        h = hours.get(cand.hour)
        if cand > now + timedelta(minutes=10) and h and h["sent"] >= 3 and h["rate"] >= 0.5:
            return cand.replace(minute=at.minute)
    return at


def quiet_kind(stats: dict[str, Any], kind: str) -> bool:
    """A kind of nudge the user almost always ignores."""
    k = stats["kinds"].get(kind)
    return bool(prefs.get("adaptive_reminders", True) and k and k["sent"] >= 6 and k["rate"] < 0.15)


def reminder_lines(stats: dict[str, Any]) -> list[str]:
    lines = []
    for h, v in stats["hours"].items():
        if v["sent"] >= 4 and v["rate"] < 0.3:
            lines.append(f"پیام‌های ساعت {h:02d} معمولاً بی‌جواب می‌مانند ({int(v['rate'] * 100)}٪ جواب)")
    for k, v in stats["kinds"].items():
        if v["sent"] >= 6 and v["rate"] < 0.15:
            lines.append(f"پیام‌های نوع {k} تقریباً همیشه نادیده گرفته می‌شوند؛ کمترشان کرده‌ام")
    return lines
