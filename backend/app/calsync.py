"""Google Calendar both ways, without OAuth or API keys — through iCal links.

Import: the user pastes their calendar's "Secret address in iCal format" (Google Calendar → Settings → the calendar →
Integrate calendar). It is read every 15 minutes; events in the next weeks (recurring meetings expanded) become
read-only events here (source="gcal"), so the planner, availability checks and leave reminders see them.

Export: /cal/<token>.ics is a private feed of what Peygir knows — classes, events, planned work blocks, deadlines
and the trips to get there — that Google Calendar can subscribe to ("Other calendars → From URL"). Google refreshes
subscribed calendars on its own schedule (usually every few hours).
"""

from __future__ import annotations

import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from . import prefs, timeutil
from .db import Event, session

log = logging.getLogger(__name__)

IMPORT_DAYS_BACK = 1
IMPORT_DAYS_AHEAD = 45
EXPORT_DAYS_AHEAD = 56
EXPORT_PARTS = ("classes", "events", "blocks", "deadlines", "trips")


# ---------------------------------------------------------------- import
def _local(value: datetime | date) -> datetime | None:
    """A DTSTART/DTEND value as naive local time; None for all-day dates."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:  # "floating" time: already local
        return value.replace(second=0, microsecond=0)
    return value.astimezone(timeutil.tz()).replace(tzinfo=None, second=0, microsecond=0)


def parse_feed(ics: bytes | str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Timed events of an iCal feed between start and end (local), recurring ones expanded."""
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(ics)
    tz = timeutil.tz()
    out = []
    for comp in recurring_ical_events.of(cal).between(start.replace(tzinfo=tz), end.replace(tzinfo=tz)):
        if str(comp.get("STATUS", "")).upper() == "CANCELLED":
            continue
        s, e = _local(comp.get("DTSTART").dt), _local(comp.get("DTEND").dt) if comp.get("DTEND") else None
        if s is None:
            continue  # all-day (holidays, birthdays…): not a time block
        if e is None:
            dur = comp.get("DURATION")
            e = s + (dur.dt if dur is not None else timedelta(hours=1))
        uid = str(comp.get("UID", "")) or f"{comp.get('SUMMARY', '')}"
        out.append({
            "external_id": f"{uid}|{s.isoformat()}"[:300],
            "title": str(comp.get("SUMMARY", "") or "(بدون عنوان)")[:200],
            "start": s,
            "end": max(e, s + timedelta(minutes=5)),
            "location": str(comp.get("LOCATION", "") or "")[:200],
            "notes": str(comp.get("DESCRIPTION", "") or "")[:500],
            "busy": str(comp.get("TRANSP", "OPAQUE")).upper() != "TRANSPARENT",
        })
    return out


def apply(s: Session, items: list[dict[str, Any]], start: datetime, end: datetime) -> dict[str, int]:
    """Upsert imported events in [start, end) and remove the ones that disappeared from the calendar."""
    existing = {e.external_id: e for e in s.query(Event).filter(Event.source == "gcal", Event.start_at >= start,
                                                                Event.start_at < end)}
    added = updated = 0
    seen = set()
    for it in items:
        if not it["busy"]:
            continue  # marked "free" in Google Calendar: doesn't block time
        seen.add(it["external_id"])
        ev = existing.get(it["external_id"])
        if ev is None:
            s.add(Event(title=it["title"], kind="meeting", start_at=it["start"], end_at=it["end"],
                        location=it["location"], notes=it["notes"], source="gcal", external_id=it["external_id"]))
            added += 1
        elif (ev.title, ev.end_at, ev.location, ev.notes) != (it["title"], it["end"], it["location"], it["notes"]):
            ev.title, ev.end_at, ev.location, ev.notes = it["title"], it["end"], it["location"], it["notes"]
            updated += 1
    removed = 0
    for key, ev in existing.items():
        if key not in seen:
            s.delete(ev)
            removed += 1
    s.flush()
    return {"added": added, "updated": updated, "removed": removed}


async def sync() -> dict[str, Any]:
    urls = [u for u in (prefs.get("gcal_ics_urls") or []) if u]
    if not urls:
        return {"ok": True, "skipped": True}
    now = timeutil.now()
    start, end = now - timedelta(days=IMPORT_DAYS_BACK), now + timedelta(days=IMPORT_DAYS_AHEAD)
    items: list[dict[str, Any]] = []
    errors = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        for url in urls:
            try:
                r = await c.get(url.replace("webcal://", "https://", 1))
                r.raise_for_status()
                items += parse_feed(r.content, start, end)
            except Exception as e:  # noqa: BLE001 - one bad feed must not stop the others
                errors.append(f"{type(e).__name__}: {str(e)[:120]}")
    status: dict[str, Any] = {"at": timeutil.iso(now), "ok": not errors, "errors": errors, "events": len(items)}
    if errors and not items:
        prefs.set("gcal_last_sync", status)
        return status
    with session() as s:
        status.update(apply(s, items, start, end))
        from . import planner

        if status["added"] or status["updated"] or status["removed"]:
            planner.invalidate(s)
    prefs.set("gcal_last_sync", status)
    if status.get("added") or status.get("updated") or status.get("removed"):
        from .bus import bus

        bus.publish("data_changed", ["schedule", "events"])
    return status


# ---------------------------------------------------------------- export
def token(s: Session | None = None, regenerate: bool = False) -> str:
    tok = prefs.get("ics_token", s=s)
    if regenerate or not tok:
        tok = secrets.token_urlsafe(24)
        prefs.set("ics_token", tok, s=s)
    return tok


def _utc(d: datetime) -> datetime:
    return d.replace(tzinfo=timeutil.tz()).astimezone(timezone.utc)


def build_feed(s: Session) -> bytes:
    import icalendar

    from . import cal, places
    from .db import PlannedBlock, Task

    parts = set(prefs.get("ics_include", s=s) or EXPORT_PARTS)
    now = timeutil.now()
    start, end = now - timedelta(days=7), now + timedelta(days=EXPORT_DAYS_AHEAD)
    feed = icalendar.Calendar()
    feed.add("prodid", "-//Peygir//personal assistant//FA")
    feed.add("version", "2.0")
    feed.add("x-wr-calname", "پیگیر")
    feed.add("x-wr-timezone", timeutil.tz().key)
    stamp = datetime.now(timezone.utc)

    def add(uid: str, title: str, a: datetime, b: datetime, location: str = "", desc: str = "") -> None:
        ev = icalendar.Event()
        ev.add("uid", f"{uid}@peygir")
        ev.add("dtstamp", stamp)
        ev.add("dtstart", _utc(a))
        ev.add("dtend", _utc(b))
        ev.add("summary", title)
        if location:
            ev.add("location", location)
        if desc:
            ev.add("description", desc)
        feed.add_component(ev)

    classes = cal.class_occurrences(s, start, end)
    events = s.query(Event).filter(Event.end_at > start, Event.start_at < end).all()
    if "classes" in parts:
        for it in classes:
            add(f"class-{it.ref_id}-{it.start:%Y%m%d%H%M}", f"🎓 {it.title}", it.start, it.end, (it.meta or {}).get("location", ""))
    if "events" in parts:
        for ev in events:
            if ev.source != "gcal":  # don't copy Google's own events back into Google
                add(f"event-{ev.id}", ev.title, ev.start_at, ev.end_at, ev.location, ev.notes)
    if "blocks" in parts:
        rows = (s.query(PlannedBlock, Task).join(Task, Task.id == PlannedBlock.task_id)
                .filter(PlannedBlock.start_at >= now - timedelta(days=1), PlannedBlock.start_at < now + timedelta(days=14),
                        PlannedBlock.status.in_(("planned", "done", "partial"))).all())
        for b, t in rows:
            add(f"block-{b.id}", f"🎯 {t.title}", b.start_at, b.end_at, desc="بلوک کاری برنامه‌ریزی‌شده در پیگیر")
    if "deadlines" in parts:
        for t in s.query(Task).filter(Task.status.in_(("todo", "doing")), Task.due_at >= start, Task.due_at < end):
            add(f"due-{t.id}", f"⏰ ددلاین: {t.title}", t.due_at - timedelta(minutes=15), t.due_at)
    if "trips" in parts:
        located = [places.Located(it.start, it.end, it.title, places.resolve(s, (it.meta or {}).get("location", "")))
                   for it in classes if (it.meta or {}).get("location")]
        located += [places.Located(ev.start_at, ev.end_at, ev.title, places.resolve(s, ev.location))
                    for ev in events if ev.location]
        for it, trip in zip(located, places.plan_trips(s, located)):
            if trip is None or trip.leave_at is None:
                continue
            if trip.prep_from:
                add(f"prep-{it.start:%Y%m%d%H%M}-{it.place.id}", "🚪 حاضر شدن", trip.prep_from, trip.leave_at)
            add(f"trip-{it.start:%Y%m%d%H%M}-{it.place.id}", f"🚗 مسیر به {it.place.name}", trip.leave_at, it.start,
                desc=f"برای «{it.title}»" + (f" از «{trip.origin.name}»" if trip.origin else ""))
    return feed.to_ical()
