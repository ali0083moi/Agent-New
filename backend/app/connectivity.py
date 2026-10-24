"""Can this server reach the outside services the assistant uses? (python -m app.connectivity; prints no secrets)"""

from __future__ import annotations

import asyncio
import socket

import httpx

from . import routing


async def _http(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": routing.USER_AGENT}) as c:
            r = await c.get(url)
        return f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return f"FAIL {type(e).__name__}"


def _tcp(host: str, port: int) -> str:
    try:
        with socket.create_connection((host, port), timeout=10):
            return "ok"
    except OSError as e:
        return f"FAIL {e}"


async def main() -> None:
    r = await routing.route((35.7036, 51.3519), (35.7575, 51.4215))
    g = await routing.geocode("دانشگاه شریف")
    print("OSM routing (Tehran test route):", f"ok {r['minutes']} min, {r['km']} km" if r else "FAIL")
    print("Nominatim geocoding:", "ok" if g else "FAIL")
    print("Google Calendar (calendar.google.com):", await _http("https://calendar.google.com/calendar/ical/en.ir%23holiday%40group.v.calendar.google.com/public/basic.ics"))
    print("Gmail IMAP (imap.gmail.com:993):", _tcp("imap.gmail.com", 993))


if __name__ == "__main__":
    asyncio.run(main())
