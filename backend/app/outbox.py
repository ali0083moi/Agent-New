"""Reliable delivery to Telegram: every outgoing message is queued and retried with backoff."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from . import timeutil
from .db import Message, Outbox, session

log = logging.getLogger(__name__)

GIVE_UP_AFTER = timedelta(hours=48)


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** max(attempts - 1, 0), 1800))


def buttons_for(message_id: int | None, meta: dict[str, Any] | None) -> list[list[dict[str, str]]]:
    """Inline keyboard for an assistant message: quick replies, snooze, undo."""
    meta = meta or {}
    if not message_id:
        return []
    rows: list[list[dict[str, str]]] = []
    qr = meta.get("quick_replies") or []
    for k in range(0, len(qr), 3):
        rows.append([{"text": q, "data": f"qr:{message_id}:{j}"} for j, q in enumerate(qr[k:k + 3], start=k)])
    extra = []
    if meta.get("followup_id"):
        extra.append({"text": "⏰ یک ساعت دیگه", "data": f"sz:{message_id}:60"})
    if meta.get("undoable"):
        extra.append({"text": "↩️ برگردون", "data": f"ud:{message_id}"})
    if extra:
        rows.append(extra)
    return rows


async def _attempt(row_id: int) -> int | None:
    from . import telegram as tg

    with session() as s:
        row = s.get(Outbox, row_id)
        if row is None or row.status != "pending":
            return None
        kind, payload, message_id = row.kind, dict(row.payload or {}), row.message_id
    chat_id = tg.owner_chat_id()
    if tg.bot is None or chat_id is None:
        return None
    try:
        if kind == "voice":
            sent = await tg.bot.send_voice(chat_id, Path(payload["path"]).read_bytes(), caption=payload.get("caption"))
        else:
            sent = await tg.bot.send(chat_id, payload.get("text", ""), buttons=payload.get("buttons"))
    except Exception as e:  # noqa: BLE001
        with session() as s:
            row = s.get(Outbox, row_id)
            row.attempts += 1
            row.last_error = str(e)[:500]
            if timeutil.now() - row.created_at > GIVE_UP_AFTER:
                row.status = "failed"
            else:
                row.next_attempt_at = timeutil.now() + _backoff(row.attempts)
        log.warning("telegram delivery %s failed (attempt will be retried): %s", row_id, e)
        return None
    with session() as s:
        row = s.get(Outbox, row_id)
        row.status, row.sent_at, row.attempts = "sent", timeutil.now(), row.attempts + 1
        if message_id and sent and kind == "text":
            m = s.get(Message, message_id)
            if m is not None:
                m.meta = {**(m.meta or {}), "telegram_message_id": sent}
    return sent


def enqueue(kind: str, payload: dict[str, Any], message_id: int | None = None) -> int | None:
    from . import telegram as tg

    if not tg.configured() or tg.owner_chat_id() is None:
        return None
    with session() as s:
        row = Outbox(kind=kind, payload=payload, message_id=message_id, next_attempt_at=timeutil.now())
        s.add(row)
        s.flush()
        return row.id


async def send(kind: str, payload: dict[str, Any], message_id: int | None = None) -> int | None:
    """Queue and try right away. Returns the Telegram message id when delivered now."""
    row_id = enqueue(kind, payload, message_id)
    if row_id is None:
        return None
    return await _attempt(row_id)


async def flush(limit: int = 20) -> int:
    with session() as s:
        ids = [
            r.id
            for r in s.query(Outbox)
            .filter(Outbox.status == "pending", Outbox.next_attempt_at <= timeutil.now())
            .order_by(Outbox.id)
            .limit(limit)
        ]
    sent = 0
    for rid in ids:
        if await _attempt(rid):
            sent += 1
    return sent


def status() -> dict[str, Any]:
    with session() as s:
        pending = s.query(Outbox).filter(Outbox.status == "pending").count()
        failed = s.query(Outbox).filter(Outbox.status == "failed").count()
        last = s.query(Outbox).filter(Outbox.last_error != "").order_by(Outbox.id.desc()).first()
        return {"pending": pending, "failed": failed, "last_error": last.last_error if last and last.status != "sent" else None}
