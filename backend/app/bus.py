"""Tiny in-process pub/sub used to push live updates to web clients (SSE)."""

from __future__ import annotations

import asyncio
import json
from typing import Any


class Bus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subs.discard(q)

    def publish(self, event: str, data: Any = None) -> None:
        payload = json.dumps({"event": event, "data": data}, ensure_ascii=False, default=str)
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass


bus = Bus()
