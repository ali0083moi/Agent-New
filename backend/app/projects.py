"""Projects (with the user's nicknames for them) and the supervisor-search contact pipeline."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import prefs, timeutil
from .db import Contact, Project, Task, WorkLog

_PREFIX = re.compile(r"^(پروژه|پروژهٔ|پروژه‌ی|project|proj)\s*", re.I)


def norm(text: str) -> str:
    t = timeutil.to_latin_digits(str(text)).lower().replace("‌", " ").strip()
    t = _PREFIX.sub("", t)
    t = t.replace("ي", "ی").replace("ك", "ک").replace("ة", "ه")
    return re.sub(r"[\s\-_.«»\"'()]+", "", t)


def resolve(s: Session, ref: Any) -> Project | None:
    """Find a project by id, name or any alias (forgiving about spaces, ZWNJ, 'پروژه' prefix)."""
    if ref is None or ref == "":
        return None
    if isinstance(ref, int) or (isinstance(ref, str) and timeutil.to_latin_digits(ref).strip().isdigit()):
        p = s.get(Project, int(timeutil.to_latin_digits(str(ref)).strip()))
        if p is not None:
            return p
    key = norm(ref)
    if not key:
        return None
    projects = s.query(Project).all()
    for p in projects:
        if key in {norm(p.name), *(norm(a) for a in p.aliases or [])}:
            return p
    best, best_len = None, 0
    for p in projects:
        for cand in [p.name, *(p.aliases or [])]:
            c = norm(cand)
            if len(c) >= 3 and (c in key or key in c) and len(c) > best_len:
                best, best_len = p, len(c)
    return best


def project_dict(s: Session, p: Project) -> dict[str, Any]:
    open_tasks = s.query(Task).filter(Task.project_id == p.id, Task.status.in_(["todo", "doing"])).count()
    week_start = timeutil.at(timeutil.week_start(timeutil.today()), "00:00")
    task_ids = [t for (t,) in s.query(Task.id).filter(Task.project_id == p.id)]
    q = s.query(func.coalesce(func.sum(WorkLog.minutes), 0)).filter(WorkLog.start_at >= week_start,
                                                                    WorkLog.end_at.is_not(None))
    q = q.filter((WorkLog.project_id == p.id) | (WorkLog.task_id.in_(task_ids or [-1])))
    nxt = (
        s.query(Task).filter(Task.project_id == p.id, Task.status.in_(["todo", "doing"]), Task.due_at.is_not(None))
        .order_by(Task.due_at).first()
    )
    return {
        "id": p.id,
        "name": p.name,
        "aliases": p.aliases or [],
        "category": p.category,
        "client": p.client,
        "description": p.description,
        "status": p.status,
        "open_tasks": open_tasks,
        "minutes_this_week": int(q.scalar() or 0),
        "next_deadline": {"title": nxt.title, "due": timeutil.iso(nxt.due_at)} if nxt else None,
    }


# ---------------------------------------------------------------- contacts (supervisor search)
CONTACT_STATUS = ["to_contact", "contacted", "replied", "interview", "positive", "rejected", "no_reply"]
CONTACT_STATUS_FA = {
    "to_contact": "باید ایمیل بزنم", "contacted": "ایمیل زده‌ام", "replied": "جواب داده", "interview": "مصاحبه/جلسه",
    "positive": "مثبت", "rejected": "رد شد", "no_reply": "بی‌جواب",
}


def resolve_contact(s: Session, ref: Any) -> Contact | None:
    if ref is None or ref == "":
        return None
    r = timeutil.to_latin_digits(str(ref)).strip()
    if r.isdigit():
        c = s.get(Contact, int(r))
        if c is not None:
            return c
    key = norm(r).replace("دکتر", "").replace("prof", "").replace("dr", "")
    best = None
    for c in s.query(Contact).all():
        n = norm(c.name).replace("دکتر", "").replace("prof", "").replace("dr", "")
        if key and (key == n or (len(key) >= 3 and (key in n or n in key))):
            best = c
            if key == n:
                break
    return best


def apply_status(c: Contact, status: str, when=None) -> None:
    """Keep last-contact and follow-up dates consistent with the pipeline status."""
    now = when or timeutil.now()
    c.status = status
    days = int(prefs.get("professor_followup_days") or 10)
    if status == "contacted":
        c.last_contact_at = now
        c.next_followup_at = now + timedelta(days=days)
    elif status in ("replied", "interview"):
        c.next_followup_at = None
    elif status in ("positive", "rejected", "no_reply"):
        c.next_followup_at = None


def contact_dict(c: Contact) -> dict[str, Any]:
    days_since = (timeutil.now() - c.last_contact_at).days if c.last_contact_at else None
    return {
        "id": c.id,
        "name": c.name,
        "kind": c.kind,
        "organization": c.organization,
        "field": c.field,
        "email": c.email,
        "url": c.url,
        "status": c.status,
        "status_fa": CONTACT_STATUS_FA.get(c.status, c.status),
        "last_contact": timeutil.iso(c.last_contact_at),
        "days_since_contact": days_since,
        "next_followup": timeutil.iso(c.next_followup_at),
        "notes": c.notes,
    }


def pipeline(s: Session) -> dict[str, Any]:
    rows = s.query(Contact).order_by(Contact.id).all()
    counts = {k: 0 for k in CONTACT_STATUS}
    for c in rows:
        counts[c.status] = counts.get(c.status, 0) + 1
    return {"counts": counts, "contacts": [contact_dict(c) for c in rows]}
