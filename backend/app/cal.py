"""Calendar: expand recurring classes, build agendas and busy intervals."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from . import prefs, timeutil
from .db import Event, PlannedBlock, RecurringEvent, Task

KIND_CATEGORY = {
    "university_class": "university",
    "language_class": "language",
    "work": "work",
    "exam": "university",
    "sport": "personal",
    "other": "personal",
}

KIND_FA = {
    "university_class": "کلاس دانشگاه",
    "language_class": "کلاس زبان",
    "work": "کار",
    "exam": "امتحان",
    "sport": "ورزش",
    "other": "سایر",
    "appointment": "قرار",
    "outing": "بیرون رفتن",
    "deadline": "ددلاین",
}

CATEGORY_FA = {
    "work": "کار",
    "university": "دانشگاه",
    "language": "زبان",
    "personal": "شخصی",
    "health": "سلامتی",
    "other": "سایر",
}


@dataclass
class Item:
    type: str  # class | event | block | deadline
    title: str
    start: datetime
    end: datetime
    ref_id: int
    category: str = "personal"
    kind: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start"] = timeutil.iso(self.start)
        d["end"] = timeutil.iso(self.end)
        return d


_EPOCH_SATURDAY = date(2000, 1, 1)  # a Saturday


def week_number(d: date, semester_start: date | str | None = None) -> int:
    if isinstance(semester_start, str) and semester_start:
        semester_start = timeutil.parse_date(semester_start)
    base = timeutil.week_start(semester_start) if semester_start else _EPOCH_SATURDAY
    return (timeutil.week_start(d) - base).days // 7 + 1


def occurs_on(rec: RecurringEvent, d: date, semester_start: date | str | None) -> bool:
    if not rec.active:
        return False
    if timeutil.weekday_key(d) not in (rec.weekdays or []):
        return False
    if rec.start_date and d < rec.start_date:
        return False
    if rec.end_date and d > rec.end_date:
        return False
    if rec.week_parity in ("odd", "even"):
        n = week_number(d, semester_start)
        if (n % 2 == 1) != (rec.week_parity == "odd"):
            return False
    return True


def class_item(rec: RecurringEvent, d: date) -> Item:
    start = timeutil.at(d, rec.start_time)
    end = timeutil.at(d, rec.end_time)
    if end <= start:  # crosses midnight
        end += timedelta(days=1)
    return Item(
        type="class",
        title=rec.title,
        start=start,
        end=end,
        ref_id=rec.id,
        category=KIND_CATEGORY.get(rec.kind, "personal"),
        kind=rec.kind,
        meta={"location": rec.location, "instructor": rec.instructor, "followup": rec.followup},
    )


def class_occurrences(s: Session, start: datetime, end: datetime, rec_id: int | None = None) -> list[Item]:
    semester_start = prefs.get("semester_start", s=s)
    q = s.query(RecurringEvent).filter(RecurringEvent.active.is_(True))
    if rec_id is not None:
        q = q.filter(RecurringEvent.id == rec_id)
    recs = q.all()
    items: list[Item] = []
    d = start.date() - timedelta(days=1)
    while d <= end.date():
        for rec in recs:
            if occurs_on(rec, d, semester_start):
                it = class_item(rec, d)
                if it.end > start and it.start < end:
                    items.append(it)
        d += timedelta(days=1)
    items.sort(key=lambda i: i.start)
    return items


def next_occurrence(s: Session, rec: RecurringEvent, after: datetime) -> Item | None:
    semester_start = prefs.get("semester_start", s=s)
    d = after.date()
    for _ in range(7 * 6):
        if occurs_on(rec, d, semester_start):
            it = class_item(rec, d)
            if it.start > after:
                return it
        d += timedelta(days=1)
    return None


def items_between(s: Session, start: datetime, end: datetime, include_deadlines: bool = True) -> list[Item]:
    items = class_occurrences(s, start, end)
    for ev in s.query(Event).filter(Event.end_at > start, Event.start_at < end).all():
        items.append(
            Item(
                type="event",
                title=ev.title,
                start=ev.start_at,
                end=ev.end_at,
                ref_id=ev.id,
                category="personal",
                kind=ev.kind,
                meta={"location": ev.location, "notes": ev.notes},
            )
        )
    blocks = (
        s.query(PlannedBlock, Task)
        .join(Task, Task.id == PlannedBlock.task_id)
        .filter(PlannedBlock.end_at > start, PlannedBlock.start_at < end)
        .all()
    )
    for b, t in blocks:
        items.append(
            Item(
                type="block",
                title=t.title,
                start=b.start_at,
                end=b.end_at,
                ref_id=b.id,
                category=t.category,
                kind="focus",
                meta={"task_id": t.id, "status": b.status, "task_status": t.status},
            )
        )
    if include_deadlines:
        for t in (
            s.query(Task)
            .filter(Task.due_at.is_not(None), Task.due_at >= start, Task.due_at < end)
            .filter(Task.status.in_(["todo", "doing"]))
            .all()
        ):
            items.append(
                Item(
                    type="deadline",
                    title=t.title,
                    start=t.due_at,
                    end=t.due_at,
                    ref_id=t.id,
                    category=t.category,
                    kind="deadline",
                    meta={"priority": t.priority},
                )
            )
    items.sort(key=lambda i: (i.start, i.type != "class"))
    return items


def agenda(s: Session, start_date: date, days: int = 1) -> list[dict[str, Any]]:
    out = []
    for i in range(days):
        d = start_date + timedelta(days=i)
        items = items_between(s, timeutil.at(d, "00:00"), timeutil.at(d + timedelta(days=1), "00:00"))
        out.append(
            {
                "date": d.isoformat(),
                "jalali": timeutil.jalali_str(d),
                "weekday": timeutil.weekday_key(d),
                "weekday_fa": timeutil.WEEKDAY_FA[timeutil.weekday_key(d)],
                "label": timeutil.fa_date(d),
                "items": [it.to_dict() for it in items],
            }
        )
    return out


def busy_intervals(
    s: Session,
    start: datetime,
    end: datetime,
    include_blocks: bool = False,
    class_buffer: int | None = None,
    exclude_event_ids: set[int] | None = None,
) -> list[tuple[datetime, datetime, str]]:
    """Hard commitments (classes with travel buffer, events), optionally planned blocks."""
    if class_buffer is None:
        class_buffer = int(prefs.get("class_buffer_minutes", s=s) or 0)
    buf = timedelta(minutes=class_buffer)
    out: list[tuple[datetime, datetime, str]] = []
    for it in class_occurrences(s, start - buf, end + buf):
        out.append((it.start - buf, it.end + buf, it.title))
    for ev in s.query(Event).filter(Event.end_at > start, Event.start_at < end).all():
        if exclude_event_ids and ev.id in exclude_event_ids:
            continue
        out.append((ev.start_at, ev.end_at, ev.title))
    if include_blocks:
        for b in (
            s.query(PlannedBlock)
            .filter(PlannedBlock.end_at > start, PlannedBlock.start_at < end, PlannedBlock.status == "planned")
            .all()
        ):
            out.append((b.start_at, b.end_at, f"block:{b.task_id}"))
    out.sort()
    return out
