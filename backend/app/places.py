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

from . import prefs, routing, timeutil
from .db import Place, TravelObservation, TravelTime

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


def _row(s: Session, a: Place, b: Place) -> TravelTime | None:
    row = s.query(TravelTime).filter(TravelTime.from_place_id == a.id, TravelTime.to_place_id == b.id).first()
    if row is None:
        row = s.query(TravelTime).filter(TravelTime.from_place_id == b.id, TravelTime.to_place_id == a.id).first()
    return row


def travel(s: Session, a: Place | None, b: Place | None, at: datetime | None = None,
           learned: dict | None = None) -> int | None:
    """Door-to-door minutes from a to b; with `at`, car/taxi trips follow that hour's traffic."""
    if a is None or b is None:
        return None
    if a.id == b.id:
        return 0
    row = _row(s, a, b)
    if row is None:
        return None
    if at is None or row.mode not in routing.TRAFFIC_MODES:
        return row.minutes
    factor, _ = routing.traffic_factor(s, at, learned)
    return max(1, round(row.minutes / routing.TYPICAL_FACTOR * factor))


def estimate(s: Session, a: Place, b: Place, at: datetime) -> dict[str, Any] | None:
    """Travel time at a given moment, with how it was worked out."""
    row = _row(s, a, b)
    if row is None:
        return None
    out: dict[str, Any] = {"from": a.name, "to": b.name, "usual_minutes": row.minutes, "mode": row.mode,
                           "minutes": travel(s, a, b, at), "at": timeutil.iso(at)}
    if row.mode in routing.TRAFFIC_MODES:
        factor, source = routing.traffic_factor(s, at)
        out.update(traffic_factor=factor, traffic_source=source)  # learned | calibrated | default
    if row.base_minutes:
        out.update(traffic_free_minutes=round(row.base_minutes), distance_km=row.distance_km)
    return out


def set_travel(s: Session, a: Place, b: Place, minutes: int, mode: str = "", both_ways: bool = True,
               observed: bool = False, at: datetime | None = None) -> TravelTime:
    """Save a travel time.

    observed=True is a real trip ("today it took 50 minutes"): it is logged with its time to learn traffic by hour;
    for metro/bus/walk it is also averaged into the usual time. Otherwise `minutes` becomes the usual time."""
    rows = []
    for x, y in ((a, b), (b, a)) if both_ways else ((a, b),):
        row = s.query(TravelTime).filter(TravelTime.from_place_id == x.id, TravelTime.to_place_id == y.id).first()
        if row is None:
            row = TravelTime(from_place_id=x.id, to_place_id=y.id, minutes=int(minutes), mode=mode or "", samples=1)
            s.add(row)
        elif observed and (mode or row.mode) not in routing.TRAFFIC_MODES:
            row.minutes = round((row.minutes * row.samples + int(minutes)) / (row.samples + 1))
            row.samples = min(row.samples + 1, 10)
        elif not observed:
            row.minutes, row.samples = int(minutes), 1
        if mode:
            row.mode = mode
        row.updated_at = timeutil.now()
        rows.append(row)
    if observed:
        s.add(TravelObservation(from_place_id=a.id, to_place_id=b.id, minutes=int(minutes), mode=mode or rows[0].mode,
                                at=at or timeutil.now() - timedelta(minutes=int(minutes))))
    s.flush()
    return rows[0]


async def fill_routes(place_id: int) -> int:
    """Traffic-free route time/distance from OSM between this place and every other place with coordinates.
    Creates the usual travel time for pairs the user hasn't given one for. Returns the number of routes found."""
    from .db import session

    with session() as s:
        p = s.get(Place, place_id)
        if p is None or p.lat is None:
            return 0
        others = [(o.id, o.lat, o.lon) for o in s.query(Place).filter(Place.id != p.id, Place.lat.is_not(None))]
        origin = (p.lat, p.lon)
    found = 0
    for oid, lat, lon in others:
        r = await routing.route(origin, (lat, lon))
        if r is None:
            continue
        found += 1
        with session() as s:
            a, b = s.get(Place, place_id), s.get(Place, oid)
            for x, y in ((a, b), (b, a)):
                row = s.query(TravelTime).filter(TravelTime.from_place_id == x.id, TravelTime.to_place_id == y.id).first()
                if row is None:
                    row = TravelTime(from_place_id=x.id, to_place_id=y.id, mode="car", samples=0,
                                     minutes=round(r["minutes"] * routing.TYPICAL_FACTOR))
                    s.add(row)
                row.base_minutes, row.distance_km, row.updated_at = r["minutes"], r["km"], timeutil.now()
    return found


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
    traffic: bool = False  # the trip time follows that hour's traffic (car/taxi)


def plan_trips(s: Session, items: list[Located]) -> list[Trip | None]:
    """Travel buffers for a day's appointments (None for items without a known place / travel time)."""
    hm = home(s)
    prep, _ = prep_minutes(s)
    learned = routing.learned_factors(s)

    def tr(a: Place | None, b: Place | None, at: datetime) -> int | None:
        return travel(s, a, b, at, learned)

    order = sorted(range(len(items)), key=lambda i: items[i].start)
    out: list[Trip | None] = [None] * len(items)
    prev: Located | None = None
    for pos, i in enumerate(order):
        it = items[i]
        if it.place is None or hm is None or it.place.id == hm.id:
            prev = it if it.place is not None else prev
            continue
        to_here = tr(hm, it.place, it.start)
        if to_here is None:
            prev = it
            continue
        origin, before, prep_from = hm, to_here + prep, it.start - timedelta(minutes=to_here + prep)
        if prev is not None and prev.place is not None and prev.place.id != hm.id and prev.end.date() == it.start.date():
            direct = tr(prev.place, it.place, it.start)
            back_home = tr(prev.place, hm, prev.end)
            gap = (it.start - prev.end).total_seconds() / 60
            # not worth going home in between → straight from the previous place
            if direct is not None and (back_home is None or gap < back_home + to_here + prep + 30):
                origin, before, prep_from = prev.place, direct, None
        nxt = next((items[j] for j in order[pos + 1:] if items[j].start.date() == it.start.date()), None)
        after = tr(it.place, hm, it.end) or 0
        if nxt is not None and nxt.place is not None and nxt.place.id != hm.id:
            back = tr(it.place, hm, it.end)
            gap = (nxt.start - it.end).total_seconds() / 60
            if back is None or gap < back + (tr(hm, nxt.place, nxt.start) or 0) + prep + 30:
                after = 0  # the next trip starts from here and is counted there
        leg = _row(s, origin, it.place)
        out[i] = Trip(before=before, after=after, leave_at=it.start - timedelta(minutes=before - (prep if prep_from else 0)),
                      prep_from=prep_from, origin=origin, traffic=bool(leg and leg.mode in routing.TRAFFIC_MODES))
        prev = it
    return out


def trip_for(s: Session, place_ref: str | None, start: datetime, end: datetime) -> dict[str, Any] | None:
    """Travel around one outing (for check_availability): minutes before/after and a short explanation."""
    pl = resolve(s, place_ref) if place_ref else None
    hm = home(s)
    if pl is None or hm is None:
        return None
    t = travel(s, hm, pl, start)
    back = travel(s, pl, hm, end)
    if t is None:
        return None
    prep, known = prep_minutes(s)
    return {"place": pl.name, "travel_minutes": t, "return_minutes": back, "prep_minutes": prep, "prep_known": known,
            "before": t + prep, "after": back if back is not None else t,
            "leave_home_at": timeutil.iso(start - timedelta(minutes=t)),
            "start_getting_ready_at": timeutil.iso(start - timedelta(minutes=t + prep))}


def place_dict(s: Session, p: Place) -> dict[str, Any]:
    hm = home(s)
    return {"id": p.id, "name": p.name, "aliases": p.aliases or [], "kind": p.kind, "address": p.address,
            "lat": p.lat, "lon": p.lon,
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
                    "both_ways": symmetric, "samples": r.samples, "estimated": r.samples == 0,
                    "distance_km": r.distance_km})
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
            lines.append("usual travel times: " + "; ".join(
                f"{r['from']} {'↔' if r['both_ways'] else '→'} {r['to']} {r['minutes']}m" + (f" ({r['mode']})" if r["mode"] else "")
                + (" (estimated from the map)" if r["estimated"] else "")
                for r in rows))
            now = timeutil.now()
            lines.append(f"car/taxi trips follow traffic by hour (now ×{routing.traffic_factor(s, now)[0]} vs free-flow); "
                         "use travel_estimate for a specific time")
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
            gaps.append(f"how long it takes to get from home to «{x.name}» and how (set_travel_time; or its address/map "
                        "link with set_place location=… so the route is measured)")
    if p.get("prep_minutes") is None:
        gaps.append("how long they need to get ready before leaving home (update_preferences prep_minutes)")
    recs = s.query(RecurringEvent).filter(RecurringEvent.active.is_(True)).all()
    if not any(r.kind == "work" for r in recs):
        gaps.append("their usual working days/hours at the company (add_recurring_events kind=work)")
    return gaps
