"""Chat sessions.

Web and Telegram always write into the *current* session, so they stay in sync. A new session starts when the user
asks for one (web button, Telegram /new) or automatically after `session_idle_hours` without messages. Only the
current session's messages are sent to the model as history; earlier sessions reach it through their summary
(the most recent one is carried over) and through long-term memory (memories + semantic recall).
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import prefs, timeutil
from .bus import bus
from .db import ChatSession, Embedding, Message, Summary

TITLE_LEN = 48


def _create(s: Session, title: str = "") -> ChatSession:
    cs = ChatSession(title=title, created_at=timeutil.now())
    s.add(cs)
    s.flush()
    prefs.set("current_session_id", cs.id, s=s)
    return cs


def current(s: Session) -> ChatSession:
    sid = prefs.get("current_session_id", s=s)
    cs = s.get(ChatSession, int(sid)) if sid else None
    if cs is None:
        cs = s.query(ChatSession).order_by(ChatSession.id.desc()).first() or _create(s)
        prefs.set("current_session_id", cs.id, s=s)
    return cs


def current_id(s: Session) -> int:
    return current(s).id


def new_session(s: Session, title: str = "") -> tuple[ChatSession, int | None]:
    """Start a new session (unless the current one is still empty). Returns (session, previous id or None)."""
    cur = current(s)
    if cur.last_message_at is None:
        if title:
            cur.title = title
        return cur, None
    cs = _create(s, title)
    bus.publish("sessions", {"current": cs.id})
    return cs, cur.id


def for_new_message(s: Session) -> tuple[int, int | None]:
    """The session a message being stored now belongs to; rolls over after a long idle gap.

    Returns (session id, id of the session that just ended or None)."""
    cur = current(s)
    hours = float(prefs.get("session_idle_hours", s=s) or 0)
    if hours > 0 and cur.last_message_at is not None and timeutil.now() - cur.last_message_at >= timedelta(hours=hours):
        cs, ended = new_session(s)
        return cs.id, ended
    return cur.id, None


def touch(s: Session, session_id: int, role: str, content: str) -> None:
    cs = s.get(ChatSession, session_id)
    if cs is None:
        return
    cs.last_message_at = timeutil.now()
    if role == "user" and not cs.title and content.strip():
        cs.title = title_from(content)


def title_from(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= TITLE_LEN else t[: TITLE_LEN - 1].rstrip() + "…"


def activate(s: Session, session_id: int) -> ChatSession | None:
    cs = s.get(ChatSession, session_id)
    if cs is not None and prefs.get("current_session_id", s=s) != cs.id:
        prefs.set("current_session_id", cs.id, s=s)
        bus.publish("sessions", {"current": cs.id})
    return cs


def listing(s: Session, limit: int = 100) -> list[dict[str, Any]]:
    counts = dict(s.query(Message.session_id, func.count()).group_by(Message.session_id).all())
    cur = current_id(s)
    rows = (
        s.query(ChatSession)
        .order_by(func.coalesce(ChatSession.last_message_at, ChatSession.created_at).desc(), ChatSession.id.desc())
        .limit(limit)
        .all()
    )
    return [to_dict(cs, counts.get(cs.id, 0), cs.id == cur) for cs in rows]


def to_dict(cs: ChatSession, count: int = 0, is_current: bool = False) -> dict[str, Any]:
    return {
        "id": cs.id,
        "title": cs.title or "گفتگوی جدید",
        "created_at": timeutil.iso(cs.created_at),
        "last_message_at": timeutil.iso(cs.last_message_at) if cs.last_message_at else None,
        "messages": count,
        "current": is_current,
        "has_summary": bool(cs.summary),
    }


def delete(s: Session, session_id: int) -> None:
    ids = [m[0] for m in s.query(Message.id).filter(Message.session_id == session_id)]
    if ids:
        s.query(Embedding).filter(Embedding.source_type == "message", Embedding.source_id.in_(ids)).delete(
            synchronize_session=False)
    s.query(Message).filter(Message.session_id == session_id).delete(synchronize_session=False)
    s.query(Summary).filter(Summary.session_id == session_id).delete(synchronize_session=False)
    cs = s.get(ChatSession, session_id)
    if cs is not None:
        s.delete(cs)
    s.flush()
    if prefs.get("current_session_id", s=s) == session_id:
        nxt = s.query(ChatSession).order_by(ChatSession.id.desc()).first()
        prefs.set("current_session_id", nxt.id if nxt else None, s=s)
        if nxt is None:
            _create(s)
    bus.publish("sessions", {"deleted": session_id})


def previous_summary(s: Session, session_id: int, within_days: int = 3) -> str:
    """Summary of the most recent earlier session, if it ended recently (continuity across sessions)."""
    prev = (
        s.query(ChatSession)
        .filter(ChatSession.id < session_id, ChatSession.summary != "")
        .order_by(ChatSession.id.desc())
        .first()
    )
    if prev is None or prev.last_message_at is None:
        return ""
    if timeutil.now() - prev.last_message_at > timedelta(days=within_days) or prev.summary.strip() == "-":
        return ""
    return prev.summary


def adopt_legacy(s: Session) -> None:
    """Messages from before sessions existed go into one session (runs at startup, once)."""
    if not s.query(Message.id).filter(Message.session_id.is_(None)).first():
        return
    first, last = s.query(func.min(Message.created_at), func.max(Message.created_at)).filter(
        Message.session_id.is_(None)).one()
    cs = ChatSession(title="گفتگوهای قبلی", created_at=first or timeutil.now(), last_message_at=last)
    s.add(cs)
    s.flush()
    s.query(Message).filter(Message.session_id.is_(None)).update({Message.session_id: cs.id}, synchronize_session=False)
    s.query(Summary).filter(Summary.session_id.is_(None)).update({Summary.session_id: cs.id}, synchronize_session=False)
    if not prefs.get("current_session_id", s=s):
        prefs.set("current_session_id", cs.id, s=s)
