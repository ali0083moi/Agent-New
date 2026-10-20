"""Weekly reports: one for the employer (work only) and one personal review."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from . import cal, planner, prefs, stats, timeutil
from .db import Goal, Report, Task, session
from .config import get_settings
from .llm import LLMError, get_llm

log = logging.getLogger(__name__)


def week_bounds(offset: int = 0, ref: date | None = None) -> tuple[date, date]:
    """Iranian week: Saturday..Friday. offset=0 current week, -1 previous week."""
    ws = timeutil.week_start(ref or timeutil.today()) + timedelta(days=7 * offset)
    return ws, ws + timedelta(days=6)


def collect(week_start: date, week_end: date, project_id: int | None = None) -> dict[str, Any]:
    from .db import Project

    with session() as s:
        p = prefs.all_prefs(s)
        st = stats.productivity(s, week_start, week_end)
        work_cats = set(p.get("work_report_categories") or ["work"])
        project = s.get(Project, project_id) if project_id else None
        nxt_start = timeutil.at(week_end + timedelta(days=1), "00:00")
        upcoming = (
            s.query(Task)
            .filter(Task.status.in_(["todo", "doing"]))
            .order_by(Task.due_at.is_(None), Task.due_at)
            .all()
        )
        open_items = [
            {
                "title": t.title,
                "category": t.category,
                "project_id": t.project_id,
                "due": timeutil.iso(t.due_at),
                "progress": t.progress,
                "remaining_minutes": planner.remaining_minutes(s, t),
                "due_next_week": bool(t.due_at and t.due_at < nxt_start + timedelta(days=7)),
            }
            for t in upcoming
        ]
        goals = [
            {"title": g.title, "target_date": timeutil.iso(g.target_date), "progress_note": g.progress_note}
            for g in s.query(Goal).filter(Goal.status == "active").all()
        ]
        from . import gamify, habits, ielts

        score = gamify.weekly_score(s, week_start)
        points = gamify.points_between(s, week_start, week_end)
        habit_list = habits.all_habits(s)
        ielts_progress = ielts.progress(s)
        def is_work(item: dict[str, Any]) -> bool:
            if project is not None:
                return item.get("project") == project.name or item.get("project_id") == project.id
            return item["category"] in work_cats

        return {
            "project": {"name": project.name, "client": project.client, "description": project.description}
            if project else None,
            "user_name": p.get("user_name") or "",
            "employer_name": (project.client if project else "") or p.get("employer_name") or "",
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "week_label": f"{timeutil.fa_date(week_start, with_weekday=False)} تا {timeutil.fa_date(week_end, with_weekday=False, with_year=True)}",
            "work_categories": sorted(work_cats),
            "stats": st,
            "work_completed": [t for t in st["completed_tasks"] if is_work(t)],
            "work_entries": [e for e in st["entries"] if is_work(e)],
            "work_open": [t for t in open_items if is_work(t)],
            "by_project": st.get("by_project", []),
            "open_items": open_items,
            "goals": goals,
            "weekly_score": score,
            "points": points,
            "habits": [{"title": h["title"], "streak": h["streak"], "last7": [d["met"] for d in h["last7"]]} for h in habit_list],
            "ielts": {"latest": ielts_progress["latest"], "target": ielts_progress["target_band"],
                      "vocab": ielts_progress["vocab"]},
        }


def _fallback_work(data: dict[str, Any]) -> str:
    st = data["stats"]
    if data.get("project"):
        work_min = sum(e["minutes"] for e in data["work_entries"])
        title = f"## گزارش هفتگی پروژه {data['project']['name']}"
    else:
        work_min = sum(c["minutes"] for c in st["by_category"] if c["category"] in data["work_categories"])
        title = "## گزارش کار هفتگی"
    lines = [f"{title}\n**بازه:** {data['week_label']}", ""]
    if data["user_name"]:
        lines.insert(1, f"**نام:** {data['user_name']}")
    lines.append(f"**مجموع زمان کاری:** {timeutil.fa_duration(work_min)}\n")
    lines.append("### کارهای انجام‌شده")
    if data["work_completed"]:
        lines += [f"- {t['title']}" for t in data["work_completed"]]
    else:
        lines.append("- (موردی ثبت نشده)")
    per_task: dict[str, int] = {}
    for e in data["work_entries"]:
        per_task[e["task"]] = per_task.get(e["task"], 0) + e["minutes"]
    if per_task:
        lines.append("\n### زمان صرف‌شده")
        lines += [f"- {k}: {timeutil.fa_duration(v)}" for k, v in sorted(per_task.items(), key=lambda x: -x[1])]
    if data["work_open"]:
        lines.append("\n### در حال انجام / هفته بعد")
        for t in data["work_open"]:
            due = f" (ددلاین: {timeutil.fa_datetime(timeutil.parse_datetime(t['due']))})" if t["due"] else ""
            lines.append(f"- {t['title']}{due}")
    return "\n".join(lines)


def _fallback_personal(data: dict[str, Any]) -> str:
    st = data["stats"]
    lines = [f"## مرور شخصی هفته\n**بازه:** {data['week_label']}", ""]
    lines.append(
        f"- کار مفید: **{timeutil.fa_duration(st['total_productive_minutes'])}** "
        f"(میانگین روزانه {timeutil.fa_duration(st['average_per_day_minutes'])}، "
        f"هدف روزانه {timeutil.fa_duration(st['daily_goal_minutes'])})"
    )
    lines.append(f"- کارهای تمام‌شده: {timeutil.fa_digits(len(st['completed_tasks']))}")
    lines.append(f"- نمره هفته: {timeutil.fa_digits(data['weekly_score']['score'])} از ۱۰۰ · امتیاز: {timeutil.fa_digits(data['points']['total'])}")
    if st["by_category"]:
        lines.append("\n### به تفکیک حوزه")
        lines += [f"- {c['label']}: {timeutil.fa_duration(c['minutes'])}" for c in st["by_category"]]
    lines.append("\n### روزبه‌روز")
    lines += [f"- {d['weekday_fa']} {d['label']}: {timeutil.fa_duration(d['minutes'])}" for d in st["days"]]
    pending = [t for t in data["open_items"] if t["due_next_week"]]
    if pending:
        lines.append("\n### ددلاین‌های پیش رو")
        lines += [f"- {t['title']}" for t in pending]
    return "\n".join(lines)


WORK_PROMPT = """You write a concise, professional weekly work report in Persian (Farsi) that the user
will hand to their employer. Use ONLY work-related data provided (categories: {cats}). Never include
personal, university or language-class items. Markdown, short sections:
"## گزارش کار هفتگی" title line with the date range, then: خلاصه (2-3 sentences), کارهای انجام‌شده
(bullets, with time spent when known), در حال انجام و برنامه هفته بعد, نکات/موانع (only if data suggests).
Write in first person from the user's perspective, polite and factual. Do not invent work that is not in the data.
If there is very little data, keep it short and honest. Use Persian digits for numbers.
When the data has `by_project`, group the completed work by project."""

PROJECT_PROMPT = """You write a concise, professional weekly progress report in Persian (Farsi) about ONE project,
which the user will send to that project's client/employer. Use only data of this project. Markdown:
"## گزارش هفتگی پروژه <name>" with the date range, then: خلاصه پیشرفت, کارهای انجام‌شده (with time spent when
known), در حال انجام و قدم‌های بعدی (with deadlines), نکات/نیاز به هماهنگی (only if data suggests). First person,
polite and factual; never invent work. Use Persian digits."""

PERSONAL_PROMPT = """You are the user's personal assistant writing their weekly self-review in Persian (Farsi),
warm but honest, like a good coach. Markdown with short sections: "## مرور هفته" with date range,
عدد‌ها (weekly score /100, points, productive time vs daily goal, tasks done, planned blocks done vs skipped),
بردهای هفته, چیزهایی که عقب افتاد, عادت‌ها (streaks), وضعیت اهداف (e.g. IELTS bands vs target, vocabulary), and 3
concrete suggestions for next week based on upcoming deadlines. Do not invent data. Use Persian digits."""


async def generate(offset: int = 0, ref: date | None = None, project_id: int | None = None) -> Report:
    ws, we = week_bounds(offset, ref)
    data = collect(ws, we, project_id)
    llm = get_llm()
    payload = json.dumps(data, ensure_ascii=False, default=str)
    model = llm.model("chat") if hasattr(llm, "model") else get_settings().chat_model
    try:
        if data["project"]:
            work = await llm.complete(PROJECT_PROMPT, f"Client: {data['employer_name']}\nData:\n{payload}",
                                      model=model, purpose="report")
            personal = ""
        else:
            work = await llm.complete(
                WORK_PROMPT.format(cats=", ".join(cal.CATEGORY_FA.get(c, c) for c in data["work_categories"])),
                f"Employer name (may be empty): {data['employer_name']}\nData:\n{payload}",
                model=model, purpose="report",
            )
            personal = await llm.complete(PERSONAL_PROMPT, f"Data:\n{payload}", model=model, purpose="report")
    except LLMError as e:
        log.warning("report LLM failed, using fallback: %s", e)
        work = _fallback_work(data)
        personal = "" if data["project"] else _fallback_personal(data)
    with session() as s:
        rep = s.query(Report).filter(Report.week_start == ws, Report.project_id.is_(project_id) if project_id is None
                                     else Report.project_id == project_id).first()
        if rep is None:
            rep = Report(week_start=ws, week_end=we, project_id=project_id)
            s.add(rep)
        rep.work_report = work
        rep.personal_report = personal
        rep.stats = {
            "weekly_score": data["weekly_score"]["score"],
            "points": data["points"]["total"],
            "total_productive_minutes": data["stats"]["total_productive_minutes"],
            "completed": len(data["stats"]["completed_tasks"]),
            "by_category": data["stats"]["by_category"],
            "days": data["stats"]["days"],
            "blocks": data["stats"]["blocks"],
        }
        rep.created_at = timeutil.now()
        s.flush()
        s.expunge(rep)
        return rep
