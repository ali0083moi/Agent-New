"""Travel times without API keys: OpenStreetMap routing + a traffic model learned from the user's own trips.

- Coordinates: from a map link or "lat,lon" text, a Telegram location pin, or an address looked up with Nominatim.
- Traffic-free driving time and distance: routing.openstreetmap.de (OSRM on OpenStreetMap data, no key).
- Traffic: there is no keyless live-traffic source for Iran (Neshan/Balad/Google need keys), so the driving time is
  multiplied by a time-of-day factor: a default Tehran rush-hour profile, replaced step by step by what the user's
  real trips show (TravelObservation: "today it took 50 minutes").
"""

from __future__ import annotations

import logging
import re
import statistics
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from . import timeutil

log = logging.getLogger(__name__)

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSRM = "https://routing.openstreetmap.de/routed-{profile}/route/v1/{mode}/{lon1},{lat1};{lon2},{lat2}"
USER_AGENT = "Peygir/1.0 (self-hosted personal assistant)"
PROFILES = {"car": ("car", "driving"), "taxi": ("car", "driving"), "": ("car", "driving"),
            "walk": ("foot", "foot"), "bike": ("bike", "bike")}
TRAFFIC_MODES = {"car", "taxi", ""}  # metro/bus/walk times don't depend much on road traffic

# Default traffic multipliers over the traffic-free time, by hour, for Tehran (workday = Sat..Wed)
_WORKDAY = {0: 1.3, 1: 1.2, 2: 1.2, 3: 1.2, 4: 1.2, 5: 1.3, 6: 1.7, 7: 2.4, 8: 2.4, 9: 2.0, 10: 1.8, 11: 1.8,
            12: 1.9, 13: 1.9, 14: 2.0, 15: 2.2, 16: 2.5, 17: 2.6, 18: 2.6, 19: 2.4, 20: 2.0, 21: 1.7, 22: 1.5, 23: 1.4}
_THURSDAY = {h: max(1.2, round(f * 0.85, 2)) for h, f in _WORKDAY.items()}
_FRIDAY = {h: 1.3 for h in range(24)}
MIN_OBSERVATIONS = 2
# a "usual" trip time the user states (or the one estimated from the route) is read as being at this traffic level
TYPICAL_FACTOR = 2.3


def day_type(d: datetime) -> str:
    k = timeutil.weekday_key(d.date())
    return "fri" if k == "fri" else "thu" if k == "thu" else "work"


def default_factor(at: datetime) -> float:
    table = {"fri": _FRIDAY, "thu": _THURSDAY, "work": _WORKDAY}[day_type(at)]
    return table[at.hour]


# ---------------------------------------------------------------- coordinates
_LINK_PATTERNS = [
    r"@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)",  # google maps …/@35.70,51.35,15z
    r"[?&](?:q|query|ll|destination|center)=(-?\d{1,2}\.\d+)(?:,|%2C)\s*(-?\d{1,3}\.\d+)",
    r"[?&]lat=(-?\d{1,2}\.\d+)&(?:lng|lon)=(-?\d{1,3}\.\d+)",  # neshan / balad
    r"geo:(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)",
    r"^\s*(-?\d{1,2}\.\d+)\s*[,،]\s*(-?\d{1,3}\.\d+)\s*$",
]


def parse_coords(text: str) -> tuple[float, float] | None:
    t = timeutil.to_latin_digits(text or "")
    for pat in _LINK_PATTERNS:
        m = re.search(pat, t)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
    return None


async def geocode(query: str) -> tuple[float, float, str] | None:
    """(lat, lon, display name) for an address or place name in Iran, via Nominatim (1 request/s policy)."""
    params = {"q": query, "format": "jsonv2", "countrycodes": "ir", "limit": 1, "accept-language": "fa"}
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as c:
            r = await c.get(NOMINATIM, params=params)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.info("geocoding failed for %r: %s", query, e)
        return None
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"]), data[0].get("display_name", "")


async def locate(text: str) -> tuple[float, float, str] | None:
    """Coordinates from a link / "lat,lon", else by looking the text up as an address."""
    c = parse_coords(text)
    if c:
        return c[0], c[1], ""
    return await geocode(text)


async def route(a: tuple[float, float], b: tuple[float, float], mode: str = "") -> dict[str, float] | None:
    """Traffic-free travel time (minutes) and distance (km) between two coordinates."""
    profile, osrm_mode = PROFILES.get(mode, PROFILES[""])
    url = OSRM.format(profile=profile, mode=osrm_mode, lat1=a[0], lon1=a[1], lat2=b[0], lon2=b[1])
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}) as c:
            r = await c.get(url, params={"overview": "false"})
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.info("routing failed: %s", e)
        return None
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    rt = data["routes"][0]
    return {"minutes": round(rt["duration"] / 60, 1), "km": round(rt["distance"] / 1000, 1)}


# ---------------------------------------------------------------- traffic, learned from real trips
def learned_factors(s: Session) -> dict[str, Any]:
    """From the user's reported trips: median (observed / traffic-free) per (day type, 2-hour slot), plus every
    trip's ratio to the default profile (how much worse/better their routes are than the default assumes)."""
    from .db import TravelObservation, TravelTime

    # traffic-free time of each route, estimated from its usual time
    base = {(r.from_place_id, r.to_place_id): r.minutes / TYPICAL_FACTOR for r in s.query(TravelTime).all() if r.minutes}
    buckets: dict[tuple[str, int], list[float]] = {}
    ratios: list[float] = []
    for o in s.query(TravelObservation).all():
        b = base.get((o.from_place_id, o.to_place_id)) or base.get((o.to_place_id, o.from_place_id))
        if not b or o.mode not in TRAFFIC_MODES:
            continue
        f = o.minutes / b
        buckets.setdefault((day_type(o.at), o.at.hour // 2), []).append(f)
        ratios.append(f / default_factor(o.at))
    return {"buckets": {k: {"factor": round(statistics.median(v), 2), "samples": len(v)} for k, v in buckets.items()},
            "ratios": ratios}


def traffic_factor(s: Session, at: datetime, learned: dict | None = None) -> tuple[float, str]:
    """(multiplier, source) for a trip starting at `at`: the user's own trips at that time when there are enough,
    else the default profile scaled by how their trips compared to it, else the default profile."""
    learned = learned_factors(s) if learned is None else learned
    hit = learned["buckets"].get((day_type(at), at.hour // 2))
    if hit and hit["samples"] >= MIN_OBSERVATIONS:
        return hit["factor"], "learned"
    if len(learned["ratios"]) >= MIN_OBSERVATIONS:
        return round(default_factor(at) * statistics.median(learned["ratios"]), 2), "calibrated"
    return default_factor(at), "default"
