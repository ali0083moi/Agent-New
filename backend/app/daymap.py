"""A human view of a day: where the user is, when they're on the road, and which free windows they have (and where).

Built from classes, work hours and events (with their places), the trips between them (places.plan_trips: getting
ready, travel, going straight from one place to the next) and the planned work blocks. Used in the assistant's
context ("you're at the company until 17:00, then 40 min to the university…"), in proactive messages and to say
where a planned work session happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from . import cal, places, prefs, timeutil
from .db import Event, PlannedBlock, Task


@dataclass
class Segment:
    start: datetime
    end: datetime
    kind: str  # commitment | prep | travel | focus | free
    title: str
    place: str = ""  # where the user is (free/commitment/focus) or "A→B" for travel

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    def line(self) -> str:
        span = f"{self.start.strftime('%H:%M')}-{self.end.strftime('%H:%M')}"
        where = f" @{self.place}" if self.place and self.kind != "travel" else (f" {self.place}" if self.place else "")
        if self.kind == "free":
            return f"{span} free{where} ({timeutil.fa_duration(self.minutes)})"
        if self.kind == "prep":
            return f"{span} getting ready{where}"
        if self.kind == "travel":
            return f"{span} on the way{where}" + (f" ({self.title})" if self.title else "")
        if self.kind == "focus":
            return f"{span} planned work: {self.title}{where}"
        return f"{span} {self.title}{where}"


def _bounds(d: date, p: dict[str, Any]) -> tuple[datetime, datetime]:
    wake = timeutil.at(d, p["wake_time"])
    sleep = timeutil.at(d, p["sleep_time"])
    if sleep <= wake:
        sleep += timedelta(days=1)
    return wake, sleep


def day_map(s: Session, d: date, p: dict[str, Any] | None = None, with_blocks: bool = True) -> list[Segment]:
    p = p or prefs.all_prefs(s)
    wake, sleep = _bounds(d, p)
    hm = places.home(s)
    home_name = hm.name if hm is not None else ""
    items: list[places.Located] = []
    labels: list[str] = []
    for it in cal.class_occurrences(s, wake, sleep):
        loc = (it.meta or {}).get("location") or ""
        items.append(places.Located(it.start, it.end, it.title, places.resolve(s, loc) if loc else None))
        labels.append(loc)
    for ev in s.query(Event).filter(Event.end_at > wake, Event.start_at < sleep).all():
        items.append(places.Located(ev.start_at, ev.end_at, ev.title, places.resolve(s, ev.location) if ev.location else None))
        labels.append(ev.location or "")
    trips = places.plan_trips(s, items)
    order = sorted(range(len(items)), key=lambda i: items[i].start)

    busy: list[Segment] = []
    for i in order:
        it, trip = items[i], trips[i]
        where = it.place.name if it.place is not None else labels[i]
        if trip is not None:
            if trip.prep_from is not None and trip.leave_at is not None and trip.leave_at > trip.prep_from:
                busy.append(Segment(trip.prep_from, trip.leave_at, "prep", "", home_name))
            if trip.leave_at is not None and trip.leave_at < it.start:
                origin = trip.origin.name if trip.origin is not None else ""
                busy.append(Segment(trip.leave_at, it.start, "travel", "traffic" if trip.traffic else "",
                                    f"{origin}→{where}"))
        busy.append(Segment(it.start, it.end, "commitment", it.title, where))
        if trip is not None and trip.after:
            busy.append(Segment(it.end, it.end + timedelta(minutes=trip.after), "travel", "", f"{where}→{home_name}"))
    if with_blocks:
        for b in (s.query(PlannedBlock).filter(PlannedBlock.start_at < sleep, PlannedBlock.end_at > wake,
                                               PlannedBlock.status.in_(["planned", "done"])).all()):
            t = s.get(Task, b.task_id)
            busy.append(Segment(b.start_at, b.end_at, "focus", t.title if t else "", ""))
    busy.sort(key=lambda x: (x.start, x.kind != "focus"))

    # where the user is between things: at home after a trip home, otherwise where the last thing ended
    out: list[Segment] = []
    cursor, here = wake, home_name
    for seg in busy:
        if seg.start > cursor:
            out.append(Segment(cursor, seg.start, "free", "", here))
        if seg.kind == "focus":
            seg.place = here
        out.append(seg)
        if seg.kind == "travel":
            here = seg.place.split("→")[-1]
        elif seg.kind == "commitment" and seg.place:
            here = seg.place
        cursor = max(cursor, seg.end)
    if sleep > cursor:
        out.append(Segment(cursor, sleep, "free", "", here))
    return [x for x in out if x.minutes > 0]


def where_now(s: Session, now: datetime | None = None, p: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the user is (probably) doing now and what comes next — for proactive messages."""
    now = now or timeutil.now()
    segs = day_map(s, now.date(), p)
    cur = next((x for x in segs if x.start <= now < x.end), None)
    nxt = next((x for x in segs if x.start > now and x.kind in ("commitment", "prep", "travel")), None)
    return {
        "now": cur.line() if cur else "outside waking hours",
        "kind": cur.kind if cur else "sleep",
        "place": cur.place if cur else "",
        "free_until": timeutil.iso(cur.end) if cur and cur.kind == "free" else None,
        "next": nxt.line() if nxt else None,
    }


def busy_now(s: Session, now: datetime | None = None) -> datetime | None:
    """End of the class / meeting the user is in right now, or of getting ready (None when they can read a message;
    on the metro / in a taxi they can)."""
    now = now or timeutil.now()
    for x in day_map(s, now.date(), with_blocks=False):
        if x.start <= now < x.end and x.kind in ("commitment", "prep"):
            return x.end
    return None


def place_at(s: Session, when: datetime, cache: dict[date, list[Segment]] | None = None) -> str:
    cache = cache if cache is not None else {}
    d = when.date()
    if d not in cache:
        cache[d] = day_map(s, d, with_blocks=False)
    for x in cache[d]:
        if x.start <= when < x.end:
            return x.place
    return ""


def describe(s: Session, d: date, p: dict[str, Any] | None = None) -> str:
    segs = day_map(s, d, p)
    return " • ".join(x.line() for x in segs if x.kind != "free" or x.minutes >= 30)


def context_lines(s: Session, now: datetime | None = None) -> list[str]:
    now = now or timeutil.now()
    p = prefs.all_prefs(s)
    out = ["\n## The user's day (where they are, travel, free windows — plan and talk with this in mind)"]
    w = where_now(s, now, p)
    out.append(f"- now: {w['now']}" + (f"; next: {w['next']}" if w["next"] else ""))
    for i, label in ((0, "today"), (1, "tomorrow")):
        d = now.date() + timedelta(days=i)
        out.append(f"- {label} ({timeutil.WEEKDAY_FA[timeutil.weekday_key(d)]}): {describe(s, d, p) or 'nothing fixed'}")
    return out
