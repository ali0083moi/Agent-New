"""Finding the item the user means by its title — so "change it" edits the existing task instead of adding a copy."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from . import timeutil
from .db import Event, Task

def norm(text: str) -> str:
    t = timeutil.to_latin_digits(text or "").lower().replace("‌", " ").replace("ي", "ی").replace("ك", "ک")
    t = re.sub(r"[«»\"'()\[\]،,.:;!?؟\-_/]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def similarity(a: str, b: str) -> float:
    """0..1; containment of a meaningful phrase counts as a strong match."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    nums_a, nums_b = set(re.findall(r"\d+", na)), set(re.findall(r"\d+", nb))
    if nums_a and nums_b and nums_a != nums_b:
        return 0.4  # "جلسه ۸" vs "جلسه ۹", "فصل ۲" vs "فصل ۳": different things
    short, long_ = sorted((na, nb), key=len)
    if len(short) >= 5 and short in long_:
        return 0.9
    ratio = SequenceMatcher(None, na, nb).ratio()
    wa, wb = set(na.split()), set(nb.split())
    overlap = len(wa & wb) / max(1, min(len(wa), len(wb)))
    return max(ratio, 0.85 * overlap if min(len(wa), len(wb)) >= 2 else 0.0)


def _ranked(rows: list[Any], ref: str, recent_first: bool = True) -> list[tuple[float, Any]]:
    scored = [(similarity(ref, r.title), r) for r in rows]
    scored = [x for x in scored if x[0] >= 0.6]
    scored.sort(key=lambda x: (x[0], x[1].id if recent_first else 0), reverse=True)
    return scored


def find_task(s: Session, ref: str, include_done: bool = False) -> tuple[Task | None, list[Task]]:
    """(best match, other close candidates). Open tasks first; recent ones win ties."""
    q = s.query(Task)
    if not include_done:
        q = q.filter(Task.status.in_(["todo", "doing"]))
    ranked = _ranked(q.all(), ref)
    if not ranked and not include_done:
        return find_task(s, ref, include_done=True)
    if not ranked:
        return None, []
    best_score = ranked[0][0]
    close = [r for sc, r in ranked[1:4] if sc >= best_score - 0.05 and sc < 1.0]
    if best_score == 1.0:
        close = []
    return ranked[0][1], close


def find_event(s: Session, ref: str, now: datetime | None = None) -> tuple[Event | None, list[Event]]:
    now = now or timeutil.now()
    rows = s.query(Event).filter(Event.end_at >= now - timedelta(days=1)).all()
    ranked = _ranked(rows, ref)
    if not ranked:
        return None, []
    best_score = ranked[0][0]
    close = [r for sc, r in ranked[1:4] if sc >= best_score - 0.05 and sc < 1.0]
    return ranked[0][1], ([] if best_score == 1.0 else close)


def _both_have_extra_words(a: str, b: str) -> bool:
    """«تمرین فصل ۱ بخش الف» vs «… بخش ب»: each title says something the other doesn't → two different things."""
    wa, wb = set(norm(a).split()), set(norm(b).split())
    return bool(wa - wb) and bool(wb - wa)


def similar_open_task(s: Session, title: str, due: datetime | None, parent_id: int | None,
                      now: datetime | None = None) -> Task | None:
    """An open task that is very likely the same thing (the model about to add a copy instead of editing)."""
    now = now or timeutil.now()
    best: tuple[float, Task] | None = None
    for t in s.query(Task).filter(Task.status.in_(["todo", "doing"])).all():
        if (t.parent_id or None) != (parent_id or None):
            continue
        sc = similarity(title, t.title)
        if sc < 0.85 or _both_have_extra_words(title, t.title):
            continue
        if due is not None and t.due_at is not None and abs(t.due_at - due) > timedelta(days=2):
            continue  # same kind of homework for another week
        if due is None or t.due_at is None:
            if t.created_at and now - t.created_at > timedelta(days=14):
                continue
        if best is None or sc > best[0]:
            best = (sc, t)
    return best[1] if best else None


def similar_event(s: Session, title: str, start: datetime) -> Event | None:
    best: tuple[float, Event] | None = None
    for ev in s.query(Event).filter(Event.start_at >= start - timedelta(hours=36),
                                    Event.start_at <= start + timedelta(hours=36)).all():
        if ev.source == "gcal":
            continue
        sc = similarity(title, ev.title)
        if sc >= 0.85 and not _both_have_extra_words(title, ev.title) and (best is None or sc > best[0]):
            best = (sc, ev)
    return best[1] if best else None
