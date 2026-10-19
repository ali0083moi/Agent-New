"""Fire-and-forget background work (memory indexing, summaries, fact extraction).

Jobs run one at a time so they never compete with each other for the SQLite writer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .config import get_settings

log = logging.getLogger(__name__)

_lock = asyncio.Lock()
_tasks: set[asyncio.Task] = set()


def spawn(name: str, factory: Callable[[], Awaitable[object]]) -> None:
    if not get_settings().background_jobs:
        return

    async def runner() -> None:
        async with _lock:
            try:
                await factory()
            except Exception:  # noqa: BLE001
                log.exception("background job %s failed", name)

    try:
        t = asyncio.get_running_loop().create_task(runner())
    except RuntimeError:
        return
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def after_turn(user_message_id: int) -> None:
    from . import memory

    spawn("facts", lambda: memory.extract_facts(user_message_id))
    spawn("index", memory.index_pending)
    spawn("summary", memory.summarize_old)
