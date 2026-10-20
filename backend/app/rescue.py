"""Rescue mode: when the workload no longer fits before the deadlines, figure out the way out."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from . import planner, prefs, timeutil


def analyze(s: Session) -> dict[str, Any]:
    feas = planner.feasibility(s)
    if not feas.at_risk:
        return {"needed": False, "min_slack_minutes": feas.min_slack}
    at_risk_ids = {t["task_id"] for t in feas.at_risk}
    shortfall = -min(t["slack_minutes"] for t in feas.at_risk)
    last_due = max(timeutil.parse_datetime(t["due"]) for t in feas.at_risk)

    # work that is not at risk and could be moved out of the way (low priority, far/no deadline first)
    movable = []
    for t in planner.schedulable_tasks(s):
        if t.id in at_risk_ids:
            continue
        due = planner.effective_due(t)
        if due is not None and due <= last_due + timedelta(hours=12):
            continue  # also needed before the crunch
        movable.append(t)
    movable.sort(key=lambda t: (-t.priority, planner.effective_due(t) is not None, planner.effective_due(t) or 0))
    postpone, freed = [], 0
    for t in movable:
        rem = planner.remaining_minutes(s, t)
        postpone.append({"task_id": t.id, "title": t.title, "priority": t.priority, "remaining_minutes": rem,
                         "due": timeutil.iso(planner.effective_due(t))})
        freed += rem
        if freed >= shortfall:
            break

    days = max(1, (last_due.date() - timeutil.today()).days + 1)
    cap = int(prefs.get("max_focus_minutes_per_day", s=s))
    extra_per_day = -(-shortfall // days)  # ceil

    extensions = [
        {**t, "who": "استاد" if t.get("category") == "university" else "مدیر/کارفرما" if t.get("category") == "work" else ""}
        for t in feas.at_risk
        if t.get("category") in ("university", "work")
    ]
    drop_or_shrink = [t for t in feas.at_risk if t.get("priority", 2) >= 3]
    return {
        "needed": True,
        "shortfall_minutes": shortfall,
        "at_risk": feas.at_risk,
        "postpone_candidates": postpone,
        "freed_by_postponing_minutes": freed,
        "extra_minutes_per_day_needed": extra_per_day,
        "days_until_last_deadline": days,
        "current_daily_cap_minutes": cap,
        "extension_candidates": extensions,
        "low_priority_at_risk": drop_or_shrink,
    }
