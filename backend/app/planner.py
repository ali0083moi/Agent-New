"""Workload math: free time, task planning (EDF) and "can I go out?" checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import cal, prefs, timeutil
from .db import PlannedBlock, Task, WorkLog

Interval = tuple[datetime, datetime]
FAR_FUTURE = datetime(2999, 1, 1)


# ---------------------------------------------------------------- task helpers
def spent_minutes(s: Session, task_id: int) -> int:
    v = (
        s.query(func.coalesce(func.sum(WorkLog.minutes), 0))
        .filter(WorkLog.task_id == task_id, WorkLog.end_at.is_not(None))
        .scalar()
    )
    return int(v or 0)


def _factors(s: Session) -> dict[str, Any]:
    """Learned estimation bias, cached for the lifetime of the DB session."""
    if "est_factors" not in s.info:
        from . import insights

        s.info["est_factors"] = insights.estimation_factors(s)
    return s.info["est_factors"]


def _parents_with_open_children(s: Session) -> set[int]:
    if "open_parents" not in s.info:
        rows = s.query(Task.parent_id).filter(Task.parent_id.is_not(None), Task.status.in_(["todo", "doing"])).all()
        s.info["open_parents"] = {r[0] for r in rows}
    return s.info["open_parents"]


def invalidate(s: Session) -> None:
    s.info.pop("est_factors", None)
    s.info.pop("open_parents", None)


def adjusted_estimate(s: Session, task: Task, default_minutes: int | None = None) -> int:
    from . import insights

    est = task.estimated_minutes or default_minutes or int(prefs.get("default_task_minutes", s=s))
    return int(round(est * insights.factor_for(task, _factors(s))))


def remaining_minutes(s: Session, task: Task, default_minutes: int | None = None) -> int:
    if task.status in ("done", "cancelled"):
        return 0
    if task.id in _parents_with_open_children(s):
        children = s.query(Task).filter(Task.parent_id == task.id, Task.status.in_(["todo", "doing"])).all()
        return sum(remaining_minutes(s, c, default_minutes) for c in children)
    est = adjusted_estimate(s, task, default_minutes)
    spent = spent_minutes(s, task.id)
    options = [est - spent]
    if task.progress:
        options.append(round(est * (1 - task.progress / 100)))
    rem = min(options)
    return max(rem, 15)  # an unfinished task always needs a little time


def open_tasks(s: Session) -> list[Task]:
    return s.query(Task).filter(Task.status.in_(["todo", "doing"])).all()


def schedulable_tasks(s: Session) -> list[Task]:
    """Open tasks the planner works on: leaf tasks (a project with open subtasks is planned via its subtasks)."""
    parents = _parents_with_open_children(s)
    tasks = [t for t in open_tasks(s) if t.id not in parents]
    by_id = {t.id: t for t in s.query(Task).filter(Task.id.in_({t.parent_id for t in tasks if t.parent_id})).all()}
    for t in tasks:  # subtasks without their own deadline inherit the project's
        if t.due_at is None and t.parent_id and by_id.get(t.parent_id) is not None:
            t.__dict__["_effective_due"] = by_id[t.parent_id].due_at
    return tasks


def effective_due(t: Task) -> datetime | None:
    return t.due_at or t.__dict__.get("_effective_due")


def _task_key(t: Task) -> tuple:
    return (effective_due(t) or FAR_FUTURE, t.priority, t.id)


# ---------------------------------------------------------------- intervals
def _subtract(free: list[Interval], busy: list[Interval]) -> list[Interval]:
    out = free
    for bs, be in busy:
        nxt: list[Interval] = []
        for fs, fe in out:
            if be <= fs or bs >= fe:
                nxt.append((fs, fe))
                continue
            if bs > fs:
                nxt.append((fs, bs))
            if be < fe:
                nxt.append((be, fe))
        out = nxt
    return [(a, b) for a, b in out if b > a]


def _minutes(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds() // 60)


def productive_window(d: date, p: dict[str, Any]) -> Interval:
    start = timeutil.at(d, p["work_start"])
    end = timeutil.at(d, p["work_end"])
    if end <= start:
        end += timedelta(days=1)
    return start, end


def round_up(dt: datetime, minutes: int = 15) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    rem = dt.minute % minutes
    return dt if rem == 0 else dt + timedelta(minutes=minutes - rem)


@dataclass
class DayFree:
    day: date
    slots: list[Interval]
    cap: int  # max focus minutes still allowed that day

    @property
    def free_minutes(self) -> int:
        return sum(_minutes(a, b) for a, b in self.slots)

    @property
    def usable(self) -> int:
        return min(self.free_minutes, self.cap)


def free_days(
    s: Session,
    start: datetime,
    end: datetime,
    extra_busy: list[Interval] | None = None,
    include_blocks: bool = False,
    p: dict[str, Any] | None = None,
) -> list[DayFree]:
    p = p or prefs.all_prefs(s)
    busy = [(a, b) for a, b, _ in cal.busy_intervals(s, start - timedelta(days=1), end + timedelta(days=1), include_blocks)]
    busy += list(extra_busy or [])
    days: list[DayFree] = []
    d = start.date() - timedelta(days=1)  # window may start the previous evening (crossing midnight)
    today = timeutil.today()
    while d <= end.date():
        ws, we = productive_window(d, p)
        ws, we = max(ws, start), min(we, end)
        if we > ws:
            slots = _subtract([(ws, we)], busy)
            cap = int(p["max_focus_minutes_per_day"])
            if d == today:
                cap = max(0, cap - logged_minutes(s, d))
            days.append(DayFree(day=d, slots=slots, cap=cap))
        d += timedelta(days=1)
    return days


def logged_minutes(s: Session, d: date) -> int:
    start, end = timeutil.at(d, "00:00"), timeutil.at(d + timedelta(days=1), "00:00")
    v = (
        s.query(func.coalesce(func.sum(WorkLog.minutes), 0))
        .filter(WorkLog.start_at >= start, WorkLog.start_at < end, WorkLog.productive.is_(True))
        .scalar()
    )
    return int(v or 0)


def capacity_until(days: list[DayFree], until: datetime) -> int:
    total = 0
    for df in days:
        clipped = [(a, min(b, until)) for a, b in df.slots if a < until]
        free = sum(_minutes(a, b) for a, b in clipped if b > a)
        total += min(free, df.cap)
    return total


# ---------------------------------------------------------------- feasibility
@dataclass
class Feasibility:
    tasks: list[dict[str, Any]] = field(default_factory=list)
    min_slack: int | None = None  # minutes; negative = cannot make it
    at_risk: list[dict[str, Any]] = field(default_factory=list)
    total_required: int = 0
    capacity_7d: int = 0


def feasibility(s: Session, extra_busy: list[Interval] | None = None, start: datetime | None = None) -> Feasibility:
    now = round_up(start or timeutil.now())
    tasks = sorted([t for t in schedulable_tasks(s) if effective_due(t)], key=_task_key)
    horizon = max([effective_due(t) for t in tasks], default=now) + timedelta(days=1)
    horizon = max(horizon, now + timedelta(days=7))
    days = free_days(s, now, horizon, extra_busy)
    res = Feasibility(capacity_7d=capacity_until(days, now + timedelta(days=7)))
    cumulative = 0
    for t in tasks:
        rem = remaining_minutes(s, t)
        cumulative += rem
        cap = capacity_until(days, effective_due(t))
        slack = cap - cumulative
        info = {
            "task_id": t.id,
            "title": t.title,
            "category": t.category,
            "priority": t.priority,
            "due": timeutil.iso(effective_due(t)),
            "remaining_minutes": rem,
            "slack_minutes": slack,
        }
        res.tasks.append(info)
        res.min_slack = slack if res.min_slack is None else min(res.min_slack, slack)
        if slack < 0:
            res.at_risk.append(info)
    res.total_required = sum(remaining_minutes(s, t) for t in schedulable_tasks(s))
    return res


# ---------------------------------------------------------------- planning
@dataclass
class PlanResult:
    blocks: list[dict[str, Any]]
    unscheduled: list[dict[str, Any]]
    warnings: list[str]


def plan(s: Session, days: int = 7, start: datetime | None = None, commit: bool = True) -> PlanResult:
    p = prefs.all_prefs(s)
    now = round_up(start or timeutil.now())
    horizon = now + timedelta(days=days)

    if commit:
        # replan: drop future, untouched blocks; keep ones in progress, imminent or already reported on
        s.query(PlannedBlock).filter(
            PlannedBlock.start_at > now + timedelta(minutes=10),
            PlannedBlock.status == "planned",
            PlannedBlock.locked.is_(False),
        ).delete()
        s.flush()

    from . import insights

    invalidate(s)
    peaks = set(insights.peak_hours(s)["hours"])
    ws_h, we_h = timeutil.parse_time(p["work_start"]).hour, timeutil.parse_time(p["work_end"]).hour
    window = set(range(ws_h, we_h)) if we_h > ws_h else set(range(ws_h, 24)) | set(range(0, we_h))
    if not peaks & window:
        peaks = set()  # peaks outside the productive window can't be used: plain EDF
    tasks = sorted(schedulable_tasks(s), key=_task_key)
    if commit:
        # blocks of tasks that became projects (have subtasks) or are no longer open
        leaf_ids = {t.id for t in tasks}
        for b in s.query(PlannedBlock).filter(PlannedBlock.end_at > now, PlannedBlock.status == "planned",
                                              PlannedBlock.locked.is_(False)).all():
            if b.task_id not in leaf_ids:
                s.delete(b)
        s.flush()
    remaining = {t.id: remaining_minutes(s, t) for t in tasks}
    # minutes already covered by blocks that are kept (in progress right now)
    for b in s.query(PlannedBlock).filter(PlannedBlock.end_at > now, PlannedBlock.status == "planned").all():
        if b.task_id in remaining:
            remaining[b.task_id] = max(0, remaining[b.task_id] - _minutes(max(b.start_at, now), b.end_at))

    focus = int(p["focus_block_minutes"])
    min_block = int(p["min_block_minutes"])
    brk = int(p["break_minutes"])
    days_free = free_days(s, now, horizon, include_blocks=True, p=p)

    new_blocks: list[PlannedBlock] = []
    per_task_day: dict[tuple[int, date], int] = {}

    for df in days_free:
        cap_left = df.cap
        for slot_start, slot_end in df.slots:
            cursor = round_up(slot_start)
            while cap_left >= 15:
                room = _minutes(cursor, slot_end)
                if room < 15:
                    break
                candidates = []
                for t in tasks:
                    if remaining[t.id] <= 0:
                        continue
                    due = effective_due(t)
                    urgent = due is not None and due - cursor <= timedelta(hours=36)
                    used_today = per_task_day.get((t.id, df.day), 0)
                    if not urgent and used_today >= 180:
                        continue
                    if due is not None and due <= cursor and due >= now:
                        continue  # can't help this one anymore inside this slot
                    candidates.append(t)
                if not candidates:
                    break
                chosen = _pick(candidates, cursor, peaks)
                if chosen is None:  # only deep work left and this isn't a peak hour: keep it for later
                    cursor = round_up(cursor + timedelta(minutes=15))
                    continue
                need = remaining[chosen.id]
                length = min(focus, need, room, cap_left)
                due = effective_due(chosen)
                if due and due > cursor:
                    length = min(length, _minutes(cursor, due))
                if length < min_block and need > length:
                    # too short for a real block of a bigger task; skip rest of this slot
                    break
                length = max(15, (length // 15) * 15) if length >= 15 else length
                if length < 15:
                    break
                block = PlannedBlock(task_id=chosen.id, start_at=cursor, end_at=cursor + timedelta(minutes=length))
                new_blocks.append(block)
                remaining[chosen.id] -= length
                cap_left -= length
                per_task_day[(chosen.id, df.day)] = per_task_day.get((chosen.id, df.day), 0) + length
                cursor = cursor + timedelta(minutes=length + brk)

    if commit:
        s.add_all(new_blocks)
        s.flush()

    titles = {t.id: t for t in tasks}
    unscheduled, warnings = [], []
    for t in tasks:
        if remaining[t.id] > 0:
            unscheduled.append({"task_id": t.id, "title": t.title, "minutes": remaining[t.id],
                                "due": timeutil.iso(effective_due(t))})
    kept = s.query(PlannedBlock).filter(PlannedBlock.end_at > now, PlannedBlock.status == "planned").all()
    for t in tasks:
        due = effective_due(t)
        if not due:
            continue
        before_due = sum(
            _minutes(max(b.start_at, now), b.end_at)
            for b in [*new_blocks, *kept]
            if b.task_id == t.id and b.end_at <= due
        )
        need = remaining_minutes(s, t)
        if before_due < need and due <= horizon:
            warnings.append(
                f"«{t.title}» تا ددلاین ({timeutil.fa_datetime(due)}) فقط {timeutil.fa_duration(before_due)} "
                f"از {timeutil.fa_duration(need)} جا می‌شود."
            )

    blocks_out = [
        {
            "id": b.id,
            "task_id": b.task_id,
            "title": titles[b.task_id].title,
            "start": timeutil.iso(b.start_at),
            "end": timeutil.iso(b.end_at),
            "minutes": _minutes(b.start_at, b.end_at),
        }
        for b in new_blocks
    ]
    return PlanResult(blocks=blocks_out, unscheduled=unscheduled, warnings=warnings)


def _pick(candidates: list[Task], cursor: datetime, peaks: set[int]) -> Task | None:
    """EDF, but match deep work to the user's peak hours when the deadline allows it.

    Returns None when only non-urgent deep work is left outside peak hours (the slot is left free).
    """
    first = candidates[0]
    due = effective_due(first)
    if not peaks or (due is not None and due - cursor <= timedelta(hours=36)):
        return first  # no learned peaks, or urgent: deadline order wins
    in_peak = cursor.hour in peaks
    for t in candidates[:4]:
        if in_peak and t.energy == "high":
            return t
        if not in_peak and t.energy != "high":
            return t
    if not in_peak and all(t.energy == "high" for t in candidates):
        return None
    return first


def sweep_missed(s: Session, now: datetime | None = None) -> int:
    """Past blocks nobody worked in become 'skipped' (feeds stats and triggers a re-plan)."""
    now = now or timeutil.now()
    n = 0
    for b in s.query(PlannedBlock).filter(PlannedBlock.status == "planned",
                                          PlannedBlock.end_at < now - timedelta(hours=2)).all():
        worked = (
            s.query(WorkLog)
            .filter(WorkLog.task_id == b.task_id, WorkLog.start_at < b.end_at,
                    (WorkLog.end_at.is_(None)) | (WorkLog.end_at > b.start_at))
            .count()
        )
        b.status = "done" if worked else "skipped"
        n += 0 if worked else 1
    s.flush()
    return n


# ---------------------------------------------------------------- availability
def check_availability(
    s: Session, start: datetime, end: datetime, buffer_minutes: int = 0,
    before_minutes: int | None = None, after_minutes: int | None = None,
) -> dict[str, Any]:
    """Can the user spend [start, end] on something else (e.g. go out)?

    Looks at hard conflicts AND whether the lost time makes deadlines infeasible. The window is widened by the
    trip there (before_minutes, default buffer_minutes) and back (after_minutes).
    """
    p = prefs.all_prefs(s)
    now = timeutil.now()
    before = buffer_minutes if before_minutes is None else before_minutes
    after = buffer_minutes if after_minutes is None else after_minutes
    win = (start - timedelta(minutes=before), end + timedelta(minutes=after))

    conflicts = []
    for it in cal.class_occurrences(s, win[0], win[1]):
        conflicts.append({"type": "class", "title": it.title, "start": timeutil.iso(it.start), "end": timeutil.iso(it.end)})
    for a, b, title in cal.busy_intervals(s, win[0], win[1], class_buffer=0):
        if any(c["title"] == title and c["type"] == "class" for c in conflicts):
            continue
        if b > win[0] and a < win[1]:
            conflicts.append({"type": "event", "title": title, "start": timeutil.iso(a), "end": timeutil.iso(b)})
    near_classes = []
    buf = int(p["class_buffer_minutes"])
    for it in cal.class_occurrences(s, win[0] - timedelta(minutes=buf), win[1] + timedelta(minutes=buf)):
        if not (it.end > win[0] and it.start < win[1]):
            near_classes.append({"title": it.title, "start": timeutil.iso(it.start), "end": timeutil.iso(it.end)})

    blocks = (
        s.query(PlannedBlock, Task)
        .join(Task, Task.id == PlannedBlock.task_id)
        .filter(PlannedBlock.end_at > win[0], PlannedBlock.start_at < win[1], PlannedBlock.status == "planned")
        .all()
    )
    moved_blocks = [
        {"task": t.title, "start": timeutil.iso(b.start_at), "end": timeutil.iso(b.end_at)} for b, t in blocks
    ]

    base = feasibility(s, start=now)
    with_out = feasibility(s, extra_busy=[win], start=now)

    # due soon after the outing: things that will feel the squeeze
    due_soon = []
    for t in schedulable_tasks(s):
        due = effective_due(t)
        if due and win[0] - timedelta(hours=12) <= due <= win[1] + timedelta(hours=36):
            due_soon.append(
                {"task_id": t.id, "title": t.title, "due": timeutil.iso(due), "remaining_minutes": remaining_minutes(s, t)}
            )

    lost = capacity_until(free_days(s, now, win[1] + timedelta(days=1)), win[1] + timedelta(days=1)) - capacity_until(
        free_days(s, now, win[1] + timedelta(days=1), extra_busy=[win]), win[1] + timedelta(days=1)
    )

    late_night = not timeutil.in_window(end.time(), timeutil.parse_time(p["wake_time"]), timeutil.parse_time(p["sleep_time"]))

    newly_at_risk = [t for t in with_out.at_risk if t["task_id"] not in {x["task_id"] for x in base.at_risk}]
    tight_threshold = 90  # minutes of slack below which we call it "tight"
    if start < now:
        verdict = "past"
    elif conflicts:
        verdict = "conflict"
    elif newly_at_risk:
        verdict = "not_feasible"
    elif base.at_risk:
        verdict = "already_behind"
    elif with_out.min_slack is not None and with_out.min_slack < tight_threshold:
        verdict = "tight"
    else:
        verdict = "ok"

    return {
        "verdict": verdict,
        "window": {"start": timeutil.iso(win[0]), "end": timeutil.iso(win[1])},
        "hard_conflicts": conflicts,
        "classes_close_by": near_classes,
        "planned_blocks_to_move": moved_blocks,
        "productive_minutes_lost": max(lost, 0),
        "slack_before_minutes": base.min_slack,
        "slack_after_minutes": with_out.min_slack,
        "tasks_newly_at_risk": newly_at_risk,
        "tasks_already_at_risk": base.at_risk,
        "due_soon": due_soon,
        "ends_after_bedtime": late_night,
        "total_open_work_minutes": base.total_required,
        "free_capacity_next_7d_minutes": with_out.capacity_7d,
        "verdict_help": {
            "ok": "مشکلی نیست، وقت کافی هست",
            "tight": "می‌رسد ولی برنامه فشرده می‌شود",
            "not_feasible": "اگر برود به ددلاین‌ها نمی‌رسد",
            "already_behind": "همین حالا هم از برنامه عقب است؛ بیرون رفتن عقب‌تر می‌اندازد",
            "conflict": "با کلاس/قرار تداخل دارد",
            "past": "زمان گذشته است",
        },
    }
