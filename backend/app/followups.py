"""Proactive follow-up engine.

`generate()` makes sure the right follow-ups exist (idempotent through dedupe keys),
`due_now()` returns the ones that should be sent now, after validating they still make sense.
The actual message composition/sending lives in `app.agent.core` / `app.notify`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from . import cal, prefs, stats, timeutil
from .db import Followup, Message, PlannedBlock, RecurringEvent, Task

log = logging.getLogger(__name__)

DEADLINE_OFFSETS = {
    "low": [timedelta(hours=24)],
    "normal": [timedelta(hours=24), timedelta(hours=3)],
    "high": [timedelta(hours=48), timedelta(hours=24), timedelta(hours=6), timedelta(hours=1)],
}
IDLE_MAX_PER_DAY = {"low": 1, "normal": 2, "high": 4}

# how late a follow-up may still be sent before it is considered stale
MAX_DELAY = {
    "block_start": timedelta(minutes=30),
    "block_end": timedelta(hours=2),
    "post_class": timedelta(hours=14),
    "daily_brief": timedelta(hours=4),
    "daily_review": timedelta(hours=3),
    "weekly_report": timedelta(hours=24),
    "idle_checkin": timedelta(minutes=20),
    "focus_long": timedelta(hours=2),
    "deadline": timedelta(hours=6),
    "task_checkin": timedelta(hours=12),
    "overdue": timedelta(hours=24),
}


def _ensure(s: Session, key: str, kind: str, due: datetime, **fields: Any) -> Followup | None:
    existing = s.query(Followup).filter(Followup.dedupe_key == key).first()
    if existing is not None:
        return None
    f = Followup(kind=kind, due_at=due, dedupe_key=key, **fields)
    s.add(f)
    s.flush()
    return f


def _intensity(p: dict[str, Any]) -> str:
    v = p.get("followup_intensity") or "normal"
    return v if v in DEADLINE_OFFSETS else "normal"


def ensure_task_followups(s: Session, t: Task, p: dict[str, Any], now: datetime) -> list[Followup]:
    """Deadline reminders, a midpoint check-in and an overdue check for one task."""
    created: list[Followup] = []
    if not t.due_at or t.status not in ("todo", "doing"):
        return created
    due_iso = timeutil.iso(t.due_at)
    base = {"task_id": t.id, "payload": {"due": due_iso}}
    for off in DEADLINE_OFFSETS[_intensity(p)]:
        at = t.due_at - off
        # skip reminders that would fire right after creating the task or are already in the past
        if at <= now + timedelta(minutes=20) or at <= (t.created_at or now) + timedelta(minutes=30):
            continue
        hours = int(off.total_seconds() // 3600)
        f = _ensure(s, f"deadline:{t.id}:{due_iso}:{hours}h", "deadline", at,
                    payload={"due": due_iso, "hours_before": hours}, task_id=t.id)
        if f:
            created.append(f)
    span = t.due_at - (t.created_at or now)
    if span >= timedelta(days=3):
        mid = (t.created_at or now) + span / 2
        if mid > now:
            f = _ensure(s, f"checkin:{t.id}:{due_iso}", "task_checkin", mid, **base)
            if f:
                created.append(f)
    f = _ensure(s, f"overdue:{t.id}:{due_iso}", "overdue", t.due_at + timedelta(minutes=30), **base)
    if f:
        created.append(f)
    return created


def next_wake(now: datetime, p: dict[str, Any]) -> datetime:
    wake = timeutil.at(now.date(), p["wake_time"])
    return wake if wake > now else wake + timedelta(days=1)


def is_quiet(now: datetime, p: dict[str, Any]) -> bool:
    return not timeutil.in_window(now.time(), timeutil.parse_time(p["wake_time"]), timeutil.parse_time(p["sleep_time"]))


def last_interaction(s: Session) -> datetime | None:
    m = s.query(Message).order_by(Message.created_at.desc()).first()
    return m.created_at if m else None


def generate(s: Session, now: datetime | None = None) -> int:
    now = now or timeutil.now()
    p = prefs.all_prefs(s)
    horizon = now + timedelta(hours=36)
    n = 0

    # 1) after classes: ask about homework / tasks for next session
    delay = timedelta(minutes=int(p.get("post_class_delay_minutes") or 15))
    for it in cal.class_occurrences(s, now - timedelta(hours=6), horizon):
        if not it.meta.get("followup"):
            continue
        due = it.end + delay
        if due < now - timedelta(hours=3):
            continue
        if _ensure(s, f"post_class:{it.ref_id}:{it.start.date().isoformat()}", "post_class", due,
                   recurring_event_id=it.ref_id,
                   payload={"date": it.start.date().isoformat(), "start": timeutil.iso(it.start),
                            "end": timeutil.iso(it.end)}):
            n += 1

    # 2) task deadlines
    for t in s.query(Task).filter(Task.status.in_(["todo", "doing"]), Task.due_at.is_not(None)).all():
        n += len(ensure_task_followups(s, t, p, now))

    # 3) planned work blocks: nudge at start, check at end
    if p.get("block_nudges", True):
        blocks = (
            s.query(PlannedBlock)
            .filter(PlannedBlock.status == "planned", PlannedBlock.start_at >= now - timedelta(minutes=10),
                    PlannedBlock.start_at <= horizon)
            .all()
        )
        for b in blocks:
            payload = {"start": timeutil.iso(b.start_at), "end": timeutil.iso(b.end_at)}
            if _ensure(s, f"block_start:{b.id}:{payload['start']}", "block_start", b.start_at, block_id=b.id,
                       task_id=b.task_id, payload=payload):
                n += 1
            if _ensure(s, f"block_end:{b.id}:{payload['end']}", "block_end", b.end_at + timedelta(minutes=5),
                       block_id=b.id, task_id=b.task_id, payload=payload):
                n += 1

    # 4) daily brief & review, weekly report
    for day in (now.date(), now.date() + timedelta(days=1)):
        for kind, pref in (("daily_brief", "daily_brief_time"), ("daily_review", "daily_review_time")):
            at = timeutil.at(day, p[pref])
            if at > now - MAX_DELAY[kind] and not _sent_today(s, kind, day) and _ensure(
                s, f"{kind}:{day.isoformat()}:{p[pref]}", kind, at, payload={"time": p[pref]}
            ):
                n += 1
        if timeutil.weekday_key(day) == p.get("weekly_report_day", "thu"):
            rep_at = timeutil.at(day, p["weekly_report_time"])
            ws = timeutil.week_start(day).isoformat()
            if rep_at > now - MAX_DELAY["weekly_report"] and _ensure(s, f"weekly_report:{ws}", "weekly_report", rep_at,
                                                                      payload={"week_start": ws}):
                n += 1

    # 5) idle check-in during the productive window
    n += _idle_checkin(s, now, p)

    # 6) a focus timer that has been running for very long (forgot to stop?)
    cur = stats.active_focus(s)
    if cur is not None and now - cur.start_at >= timedelta(hours=3):
        if _ensure(s, f"focus_long:{cur.id}", "focus_long", now, payload={"log_id": cur.id}):
            n += 1
    return n


def _sent_today(s: Session, kind: str, day) -> bool:
    return (
        s.query(Followup)
        .filter(Followup.kind == kind, Followup.status == "sent", Followup.dedupe_key.like(f"{kind}:{day.isoformat()}:%"))
        .count()
        > 0
    )


def _idle_checkin(s: Session, now: datetime, p: dict[str, Any]) -> int:
    hours = float(p.get("idle_checkin_hours") or 0)
    if hours <= 0 or is_quiet(now, p):
        return 0
    ws, we = timeutil.at(now.date(), p["work_start"]), timeutil.at(now.date(), p["work_end"])
    if we <= ws:
        we += timedelta(days=1)
    if not (ws + timedelta(hours=1) <= now < we - timedelta(minutes=30)):
        return 0
    last = last_interaction(s)
    if last is not None and now - last < timedelta(hours=hours):
        return 0
    if stats.active_focus(s) is not None:
        return 0
    if any(it.start <= now < it.end for it in cal.items_between(s, now - timedelta(hours=4), now + timedelta(minutes=1),
                                                                include_deadlines=False) if it.type != "block"):
        return 0  # in class / at an event
    if s.query(Followup).filter(Followup.kind == "idle_checkin", Followup.status == "pending").count():
        return 0
    day = now.date().isoformat()
    sent_today = s.query(Followup).filter(Followup.dedupe_key.like(f"idle:{day}:%")).count()
    if sent_today >= IDLE_MAX_PER_DAY[_intensity(p)]:
        return 0
    return 1 if _ensure(s, f"idle:{day}:{sent_today + 1}", "idle_checkin", now) else 0


def _still_valid(s: Session, f: Followup, now: datetime) -> bool:
    if f.task_id is not None and f.kind != "custom":
        t = s.get(Task, f.task_id)
        if t is None or t.status not in ("todo", "doing"):
            return False
        due = (f.payload or {}).get("due")
        if due and timeutil.iso(t.due_at) != due:
            return False  # deadline changed; new reminders get generated
    if f.block_id is not None:
        b = s.get(PlannedBlock, f.block_id)
        if b is None or b.status != "planned":
            return False
        if (f.payload or {}).get("start") != timeutil.iso(b.start_at):
            return False
        if f.kind == "block_start":
            cur = stats.active_focus(s)
            if cur is not None and cur.task_id == b.task_id:
                return False
    if f.recurring_event_id is not None:
        r = s.get(RecurringEvent, f.recurring_event_id)
        if r is None or not r.active:
            return False
    if f.kind == "focus_long" and stats.active_focus(s) is None:
        return False
    if f.kind in ("daily_brief", "daily_review"):
        pref = prefs.get(f"{f.kind}_time", s=s)
        if (f.payload or {}).get("time") != pref:
            return False  # the user changed the time; a new one is generated
    limit = MAX_DELAY.get(f.kind)
    if limit is not None and now - f.due_at > limit:
        return False
    return True


def due_now(s: Session, now: datetime | None = None) -> list[Followup]:
    """Pending follow-ups that should go out now. Stale/invalid ones get cancelled,
    ones falling in quiet hours get postponed to wake-up time."""
    now = now or timeutil.now()
    p = prefs.all_prefs(s)
    out = []
    rows = s.query(Followup).filter(Followup.status == "pending", Followup.due_at <= now).order_by(Followup.due_at).all()
    for f in rows:
        if not _still_valid(s, f, now):
            f.status = "cancelled"
            continue
        if f.kind != "custom" and is_quiet(now, p):
            if f.kind in ("block_start", "block_end", "idle_checkin", "focus_long", "daily_review"):
                f.status = "cancelled"
            else:
                f.due_at = next_wake(now, p) + timedelta(minutes=15)
            continue
        out.append(f)
    s.flush()
    return out
