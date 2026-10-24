"""Places, travel times and getting-ready time — so plans include the trip, not only the appointment.

A class or event whose `location` matches a known place (by name or nickname) gets travel time around it:
from home it needs getting ready (`prep_minutes`) + the trip there, and the trip back afterwards; if the user
comes straight from another appointment (the gap is too short to go home in between), only the direct trip counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from . import prefs, timeutil
from .db import Place, TravelTime

DEFAULT_PREP = 30
KIND_FA = {"home": "خانه", "work": "محل کار", "university": "دانشگاه", "class": "آموزشگاه", "gym": "باشگاه", "other": "سایر"}


def norm(text: str) -> str:
    t = timeutil.to_latin_digits(text or "").lower().replace("‌", " ")
    t = re.sub(r"[ي]", "ی", t).replace("ك", "ک")
    t = re.sub(r"^(محل|ساختمان)\s+", "", t.strip())
    return re.sub(r"\s+", " ", t).strip()


def resolve(s: Session, ref: str | int | None) -> Place | None:
    if ref in (None, ""):
        return None
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        return s.get(Place, int(ref))
    key = norm(str(ref))
    places = s.query(Place).all()
    for p in places:
        if key == norm(p.name) or key in {norm(a) for a in p.aliases or []}:
            return p
    # a location such as "دانشگاه شریف، کلاس ۲۰۳" or "شرکت (طبقه ۳)" → the place whose name/nickname it contains
    best = None
    for p in places:
        for n in [p.name, *(p.aliases or [])]:
            nn = norm(n)
            if len(nn) >= 3 and (nn in key or key in nn) and (best is None or len(nn) > best[0]):
                best = (len(nn), p)
    return best[1] if best else None


def home(s: Session) -> Place | None:
    return s.query(Place).filter(Place.kind == "home").first()


def prep_minutes(s: Session) -> tuple[int, bool]:
    """(minutes to get ready before leaving home, whether the user told us)."""
    v = prefs.get("prep_minutes", s=s)
    return (int(v), True) if v is not None else (DEFAULT_PREP, False)


def travel(s: Session, a: Place | None, b: Place | None) -> int | None:
    if a is None or b is None:
        return None
    if a.id == b.id:
        return 0
    row = s.query(TravelTime).filter(TravelTime.from_place_id == a.id, TravelTime.to_place_id == b.id).first()
    if row is None:
        row = s.query(TravelTime).filter(TravelTime.from_place_id == b.id, TravelTime.to_place_id == a.id).first()
    return row.minutes if row else None


def set_travel(s: Session, a: Place, b: Place, minutes: int, mode: str = "", both_ways: bool = True,
               observed: bool = False) -> TravelTime:
    """Save a travel time. observed=True blends a reported actual trip into the stored average."""
    rows = []
    for x, y in ((a, b), (b, a)) if both_ways else ((a, b),):
        row = s.query(TravelTime).filter(TravelTime.from_place_id == x.id, TravelTime.to_place_id == y.id).first()
        if row is None:
            row = TravelTime(from_place_id=x.id, to_place_id=y.id, minutes=int(minutes), mode=mode or "", samples=1)
            s.add(row)
        elif observed:
            row.minutes = round((row.minutes * row.samples + int(minutes)) / (row.samples + 1))
            row.samples = min(row.samples + 1, 10)
        else:
            row.minutes, row.samples = int(minutes), 1
        if mode:
            row.mode = mode
        row.updated_at = timeutil.now()
        rows.append(row)
    s.flush()
    return rows[0]


@dataclass
class Located:
    start: datetime
    end: datetime
    title: str
    place: Place | None


@dataclass
class Trip:
    before: int  # minutes blocked before the start (prep + travel)
    after: int  # minutes blocked after the end (travel back home, if this is the last stop)
    leave_at: datetime | None  # when to walk out of the door
    prep_from: datetime | None  # when to start getting ready (only when leaving from home)
    origin: Place | None


def plan_trips(s: Session, items: list[Located]) -> list[Trip | None]:
    """Travel buffers for a day's appointments (None for items without a known place / travel time)."""
    hm = home(s)
    prep, _ = prep_minutes(s)
    order = sorted(range(len(items)), key=lambda i: items[i].start)
    out: list[Trip | None] = [None] * len(items)
    prev: Located | None = None
    for pos, i in enumerate(order):
        it = items[i]
        if it.place is None or hm is None or it.place.id == hm.id:
            prev = it if it.place is not None else prev
            continue
        to_here = travel(s, hm, it.place)
        if to_here is None:
            prev = it
            continue
        origin, before, prep_from = hm, to_here + prep, it.start - timedelta(minutes=to_here + prep)
        if prev is not None and prev.place is not None and prev.place.id != hm.id and prev.end.date() == it.start.date():
            direct = travel(s, prev.place, it.place)
            back_home = travel(s, prev.place, hm)
            gap = (it.start - prev.end).total_seconds() / 60
            # not worth going home in between → straight from the previous place
            if direct is not None and (back_home is None or gap < back_home + to_here + prep + 30):
                origin, before, prep_from = prev.place, direct, None
        nxt = next((items[j] for j in order[pos + 1:] if items[j].start.date() == it.start.date()), None)
        after = travel(s, it.place, hm) or 0
        if nxt is not None and nxt.place is not None and nxt.place.id != hm.id:
            back = travel(s, it.place, hm)
            gap = (nxt.start - it.end).total_seconds() / 60
            if back is None or gap < back + (travel(s, hm, nxt.place) or 0) + prep + 30:
                after = 0  # the next trip starts from here and is counted there
        out[i] = Trip(before=before, after=after, leave_at=it.start - timedelta(minutes=before - (prep if prep_from else 0)),
                      prep_from=prep_from, origin=origin)
        prev = it
    return out


def trip_for(s: Session, place_ref: str | None, start: datetime, end: datetime) -> dict[str, Any] | None:
    """Travel around one outing (for check_availability): minutes before/after and a short explanation."""
    pl = resolve(s, place_ref) if place_ref else None
    hm = home(s)
    if pl is None or hm is None:
        return None
    t = travel(s, hm, pl)
    if t is None:
        return None
    prep, known = prep_minutes(s)
    return {"place": pl.name, "travel_minutes": t, "prep_minutes": prep, "prep_known": known,
            "before": t + prep, "after": t,
            "leave_home_at": timeutil.iso(start - timedelta(minutes=t)),
            "start_getting_ready_at": timeutil.iso(start - timedelta(minutes=t + prep))}


def place_dict(s: Session, p: Place) -> dict[str, Any]:
    hm = home(s)
    return {"id": p.id, "name": p.name, "aliases": p.aliases or [], "kind": p.kind, "address": p.address,
            "from_home_minutes": travel(s, hm, p) if hm and hm.id != p.id else None}


def travel_rows(s: Session) -> list[dict[str, Any]]:
    names = {p.id: p.name for p in s.query(Place).all()}
    seen, out = set(), []
    for r in s.query(TravelTime).order_by(TravelTime.id):
        key = tuple(sorted((r.from_place_id, r.to_place_id)))
        rev = s.query(TravelTime).filter(TravelTime.from_place_id == r.to_place_id,
                                         TravelTime.to_place_id == r.from_place_id).first()
        symmetric = rev is not None and rev.minutes == r.minutes
        if symmetric and key in seen:
            continue
        seen.add(key)
        out.append({"id": r.id, "from": names.get(r.from_place_id, "?"), "to": names.get(r.to_place_id, "?"),
                    "from_id": r.from_place_id, "to_id": r.to_place_id, "minutes": r.minutes, "mode": r.mode,
                    "both_ways": symmetric, "samples": r.samples})
    return out


def context_lines(s: Session) -> list[str]:
    places = s.query(Place).order_by(Place.id).all()
    prep, prep_known = prep_minutes(s)
    lines = ["\n## Places & travel"]
    if places:
        lines += [f"- [place {p.id}] {p.name} ({p.kind})" + (f" — nicknames: {', '.join(p.aliases)}" if p.aliases else "")
                  for p in places]
        rows = travel_rows(s)
        if rows:
            lines.append("travel times: " + "; ".join(
                f"{r['from']} {'↔' if r['both_ways'] else '→'} {r['to']} {r['minutes']}m" + (f" ({r['mode']})" if r["mode"] else "")
                for r in rows))
    else:
        lines.append("- (no places yet)")
    lines.append(f"getting ready before leaving home: {prep} min" + ("" if prep_known else " (assumed — not asked yet)"))
    gaps = profile_gaps(s)
    if gaps:
        lines.append("\n## Not known yet (ask ONE of these casually when the moment fits — at most one per conversation)")
        lines += [f"- {g}" for g in gaps[:4]]
    return lines


def profile_gaps(s: Session) -> list[str]:
    """What the assistant still needs to learn to plan the user's days realistically."""
    from .db import RecurringEvent

    p = prefs.all_prefs(s)
    places = s.query(Place).all()
    gaps = []
    if not p.get("user_name"):
        gaps.append("the user's name (save with update_preferences user_name)")
    if not any(x.kind == "home" for x in places):
        gaps.append("where they live (add a place of kind home, e.g. «خانه»)")
    if not any(x.kind == "work" for x in places) and p.get("employer_name") != "-":
        gaps.append("where they work and how long the commute from home takes (set_place + set_travel_time)")
    hm = home(s)
    for x in places:
        if hm and x.id != hm.id and travel(s, hm, x) is None:
            gaps.append(f"how long it takes to get from home to «{x.name}» (set_travel_time)")
    if p.get("prep_minutes") is None:
        gaps.append("how long they need to get ready before leaving home (update_preferences prep_minutes)")
    recs = s.query(RecurringEvent).filter(RecurringEvent.active.is_(True)).all()
    if not any(r.kind == "work" for r in recs):
        gaps.append("their usual working days/hours at the company (add_recurring_events kind=work)")
    return gaps
