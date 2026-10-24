"""University applications: one row per university/program, a checklist of what it needs, deadline reminders."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from . import timeutil
from .db import Application, ApplicationItem, Contact

STATUS = ["researching", "preparing", "submitted", "interview", "admitted", "rejected", "waitlisted", "withdrawn"]
STATUS_FA = {"researching": "در حال بررسی", "preparing": "در حال آماده‌سازی", "submitted": "ارسال شد",
             "interview": "مصاحبه", "admitted": "پذیرش 🎉", "rejected": "رد شد", "waitlisted": "لیست انتظار",
             "withdrawn": "منصرف شدم"}
ITEM_KINDS = ["sop", "cv", "transcript", "ielts", "gre", "recommendation", "fee", "portfolio", "other"]
ITEM_STATUS = ["todo", "in_progress", "requested", "done"]
ACTIVE = ("researching", "preparing")
# reminders before a deadline (days)
REMIND_DAYS = (30, 14, 7, 3, 1)


def default_items(recommendations: int = 2) -> list[tuple[str, str]]:
    items = [("sop", "انگیزه‌نامه (SOP)"), ("cv", "رزومه (CV)"), ("transcript", "ریزنمرات"),
             ("ielts", "ارسال نمره آیلتس")]
    items += [("recommendation", f"توصیه‌نامه {i + 1}") for i in range(max(0, recommendations))]
    items.append(("fee", "پرداخت هزینه اپلای"))
    return items


def _norm(t: str) -> str:
    t = timeutil.to_latin_digits(t or "").lower().replace("‌", " ")
    t = re.sub(r"\b(university|of|the|دانشگاه)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def resolve(s: Session, ref: str | int | None) -> Application | None:
    if ref in (None, ""):
        return None
    if isinstance(ref, int) or str(ref).isdigit():
        return s.get(Application, int(ref))
    key = _norm(str(ref))
    apps = s.query(Application).all()
    for a in apps:
        if key in (_norm(a.university), _norm(f"{a.university} {a.program}")):
            return a
    for a in apps:
        if key and (key in _norm(f"{a.university} {a.program} {a.country}") or _norm(a.university) in key):
            return a
    return None


def resolve_item(s: Session, app: Application, ref: str | int) -> ApplicationItem | None:
    items = s.query(ApplicationItem).filter(ApplicationItem.application_id == app.id).order_by(ApplicationItem.id).all()
    if isinstance(ref, int) or str(ref).isdigit():
        n = int(ref)
        return next((i for i in items if i.id == n), None)
    key = _norm(str(ref))
    for i in items:
        if key in (i.kind, _norm(i.title)) or key in _norm(i.title) or (i.person and key in _norm(i.person)):
            return i
    # "recommendation" → the first one still open
    return next((i for i in items if i.kind == key and i.status != "done"), None)


def item_dict(i: ApplicationItem) -> dict[str, Any]:
    return {"id": i.id, "kind": i.kind, "title": i.title, "status": i.status, "person": i.person,
            "due": timeutil.iso(i.due) if i.due else None, "notes": i.notes}


def app_dict(s: Session, a: Application) -> dict[str, Any]:
    items = s.query(ApplicationItem).filter(ApplicationItem.application_id == a.id).order_by(ApplicationItem.id).all()
    now = timeutil.now()
    contact = s.get(Contact, a.contact_id) if a.contact_id else None
    return {
        "id": a.id, "university": a.university, "program": a.program, "country": a.country, "degree": a.degree,
        "deadline": timeutil.iso(a.deadline) if a.deadline else None,
        "days_left": (a.deadline.date() - now.date()).days if a.deadline else None,
        "status": a.status, "status_fa": STATUS_FA.get(a.status, a.status),
        "portal_url": a.portal_url, "fee": a.fee, "notes": a.notes,
        "contact": {"id": contact.id, "name": contact.name} if contact else None,
        "items": [item_dict(i) for i in items],
        "done": sum(1 for i in items if i.status == "done"), "total": len(items),
        "missing": [i.title for i in items if i.status != "done"],
        "submitted_at": timeutil.iso(a.submitted_at) if a.submitted_at else None,
    }


def context_lines(s: Session, full: bool) -> list[str]:
    apps = s.query(Application).order_by(Application.deadline.is_(None), Application.deadline).all()
    if not apps:
        return []
    if not full:
        active = [a for a in apps if a.status in ACTIVE]
        nxt = next((a for a in active if a.deadline), None)
        return [f"\nApplications: {len(apps)} tracked, {len(active)} in preparation"
                + (f"; next deadline {a_label(nxt)} {timeutil.iso(nxt.deadline)}" if nxt else "")
                + " (load_tools apply for details)."]
    lines = ["\n## University applications"]
    for a in apps:
        d = app_dict(s, a)
        lines.append(
            f"- [application {a.id}] {a_label(a)} ({a.country or '?'}, {a.degree}) — {a.status}"
            + (f", deadline {d['deadline']} ({d['days_left']} days)" if a.deadline else "")
            + f", checklist {d['done']}/{d['total']}" + (f", missing: {', '.join(d['missing'][:6])}" if d["missing"] else "")
        )
    return lines


def a_label(a: Application) -> str:
    return a.university + (f" — {a.program}" if a.program else "")


def ensure_followups(s: Session, now: datetime) -> int:
    """Deadline reminders for applications still being prepared (from followups.generate)."""
    from .followups import _ensure

    n = 0
    for a in s.query(Application).filter(Application.status.in_(ACTIVE), Application.deadline.is_not(None)).all():
        for days in REMIND_DAYS:
            at = timeutil.at((a.deadline - timedelta(days=days)).date(), "10:00")
            if now - timedelta(hours=12) <= at <= now + timedelta(hours=36):
                if _ensure(s, f"app:{a.id}:{timeutil.iso(a.deadline)}:{days}", "application_deadline", max(at, now),
                           application_id=a.id, payload={"days": days, "deadline": timeutil.iso(a.deadline)}):
                    n += 1
    return n
