"""Store assistant/user messages and fan them out to the web (SSE) and Telegram."""

from __future__ import annotations

import logging
from typing import Any

from . import prefs, serialize, timeutil
from .bus import bus
from .db import Message, session

log = logging.getLogger(__name__)


def store_message(role: str, content: str, channel: str, kind: str = "chat", meta: dict[str, Any] | None = None) -> dict[str, Any]:
    with session() as s:
        m = Message(role=role, content=content, channel=channel, kind=kind, meta=meta or {}, created_at=timeutil.now())
        s.add(m)
        s.flush()
        data = serialize.message(m)
        if role == "user":
            prefs.set("last_user_message_at", timeutil.iso(m.created_at), s=s)
    bus.publish("message", data)
    return data


def update_meta(message_id: int, **meta: Any) -> None:
    with session() as s:
        m = s.get(Message, message_id)
        if m is not None:
            m.meta = {**(m.meta or {}), **meta}


async def deliver(content: str, kind: str, meta: dict[str, Any] | None = None, telegram: bool = True) -> dict[str, Any]:
    """A proactive assistant message: save it, show it on the web, push it to Telegram."""
    data = store_message("assistant", content, "system", kind=kind, meta=meta)
    if telegram:
        from . import telegram as tg

        try:
            tg_id = await tg.send_to_owner(content, quick_replies=(meta or {}).get("quick_replies"), message_id=data["id"])
            if tg_id:
                update_meta(data["id"], telegram_message_id=tg_id)
        except Exception:  # noqa: BLE001
            log.exception("telegram delivery failed")
    return data


async def mirror_web_turn(user_text: str, reply: str, quick_replies: list[str] | None = None) -> None:
    """Keep Telegram in sync when the user chats on the web UI."""
    if not prefs.get("mirror_web_to_telegram", True):
        return
    from . import telegram as tg

    quoted = user_text.strip()
    if len(quoted) > 600:
        quoted = quoted[:600] + "…"
    quoted = "\n".join(f"> {line}" for line in quoted.splitlines() or [""])
    try:
        await tg.send_to_owner(f"💻 _از وب:_\n{quoted}\n\n{reply}", quick_replies=quick_replies)
    except Exception:  # noqa: BLE001
        log.exception("telegram mirror failed")
