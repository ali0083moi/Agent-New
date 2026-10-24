"""Store assistant/user messages and fan them out to the web (SSE), Telegram (outbox) and web push."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from . import prefs, serialize, timeutil
from .bus import bus
from .config import get_settings
from .db import Message, session

log = logging.getLogger(__name__)


def store_message(role: str, content: str, channel: str, kind: str = "chat", meta: dict[str, Any] | None = None,
                  session_id: int | None = None) -> dict[str, Any]:
    from . import jobs, sessions

    ended = None
    with session() as s:
        if session_id is None:
            session_id, ended = sessions.for_new_message(s)
        m = Message(role=role, content=content, channel=channel, kind=kind, meta=meta or {}, created_at=timeutil.now(),
                    session_id=session_id)
        s.add(m)
        s.flush()
        sessions.touch(s, session_id, role, content)
        data = serialize.message(m)
        if role == "user":
            prefs.set("last_user_message_at", timeutil.iso(m.created_at), s=s)
            from . import insights

            insights.mark_responded(s, m.created_at)
    if ended is not None:
        jobs.session_ended(ended)
        bus.publish("sessions", {"current": session_id})
    bus.publish("message", data)
    return data


def update_meta(message_id: int, **meta: Any) -> None:
    with session() as s:
        m = s.get(Message, message_id)
        if m is not None:
            m.meta = {**(m.meta or {}), **meta}
            data = serialize.message(m)
    if m is not None:
        bus.publish("message", data)


async def deliver(content: str, kind: str, meta: dict[str, Any] | None = None, telegram: bool = True) -> dict[str, Any]:
    """A proactive assistant message: save it, show it on the web, push it to Telegram and the browser."""
    from . import outbox

    data = store_message("assistant", content, "system", kind=kind, meta=meta)
    if telegram:
        try:
            await outbox.send("text", {"text": content, "buttons": outbox.buttons_for(data["id"], data["meta"])}, data["id"])
        except Exception:  # noqa: BLE001
            log.exception("telegram delivery failed")
    _push_later(content, kind)
    if prefs.get("voice_replies") == "always":
        await send_voice_reply(data, user_sent_voice=False)
    return data


def _push_later(content: str, kind: str) -> None:
    if not prefs.get("push_enabled", True):
        return
    from . import push

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(push.notify_all("پیگیر", plain_text(content)[:180], tag=kind))


def plain_text(md: str) -> str:
    t = re.sub(r"```.*?```", "", md, flags=re.S)
    t = re.sub(r"[*_`#>|~]", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    return re.sub(r"\n{2,}", "\n", t).strip()


async def send_voice_reply(message: dict[str, Any], user_sent_voice: bool) -> None:
    """Text-to-speech version of an assistant reply on Telegram (per the voice_replies preference)."""
    mode = prefs.get("voice_replies") or "off"
    if mode == "off" or (mode == "when_voice" and not user_sent_voice):
        return
    from . import outbox, telegram

    if telegram.owner_chat_id() is None or not telegram.configured():
        return
    from .llm import LLMError, get_llm

    text = plain_text(message["content"])
    if len(text) > 1500:
        text = text[:1500].rsplit("\n", 1)[0] + " …"
    if not text:
        return
    try:
        audio = await get_llm().speak(text, fmt="opus")
    except LLMError as e:
        log.info("voice reply skipped: %s", e)
        return
    folder = Path(get_settings().upload_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"tts-{uuid.uuid4().hex[:10]}.ogg"
    path.write_bytes(audio)
    await outbox.send("voice", {"path": str(path)}, message.get("id"))


async def mirror_web_turn(user_text: str, reply: dict[str, Any]) -> None:
    """Keep Telegram in sync when the user chats on the web UI."""
    if not prefs.get("mirror_web_to_telegram", True):
        return
    from . import outbox

    quoted = user_text.strip()
    if len(quoted) > 600:
        quoted = quoted[:600] + "…"
    quoted = "\n".join(f"> {line}" for line in quoted.splitlines() or [""])
    try:
        await outbox.send("text", {"text": f"💻 _از وب:_\n{quoted}\n\n{reply['content']}",
                                   "buttons": outbox.buttons_for(reply["id"], {"undoable": reply["meta"].get("undoable")})},
                          reply["id"])
    except Exception:  # noqa: BLE001
        log.exception("telegram mirror failed")
