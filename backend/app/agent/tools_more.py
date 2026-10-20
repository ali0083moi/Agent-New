"""More tools: projects & exams, rescue mode, undo, habits, commitments, IELTS coach, insights."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from .. import gamify, habits, ielts, insights, journal, planner, prefs, rescue, serialize, timeutil
from ..db import Commitment, Event, Habit, HabitLog, Journal, PlannedBlock, Task, VocabWord, session
from .tools import CATEGORIES, DT_HELP, Ctx, _CONFIRM, _cat, _get, tool


# ------------------------------------------------------------------ projects & exams
@tool(
    "break_down_task",
    "Split a big task/project into subtasks or milestones (each with an estimate and optionally its own "
    "intermediate deadline). The planner then schedules the subtasks; the project completes when all are done. "
    "Use for anything bigger than ~3 hours or spanning several days.",
    {
        "task_id": {"type": "integer"},
        "subtasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "estimated_minutes": {"type": "integer"},
                    "due": {"type": "string", "description": DT_HELP},
                    "energy": {"type": "string", "enum": ["high", "normal", "low"]},
                },
                "required": ["title", "estimated_minutes"],
            },
        },
    },
    ["task_id", "subtasks"],
    changes=("tasks", "followups"),
)
def break_down_task(s: Session, ctx: Ctx, task_id: int, subtasks: list[dict[str, Any]]):
    parent = _get(s, Task, task_id, "تسک")
    created = []
    for i, st in enumerate(subtasks):
        due = timeutil.parse_datetime(st["due"]) if st.get("due") else None
        if due and parent.due_at and due > parent.due_at:
            due = parent.due_at
        t = Task(title=str(st["title"]).strip(), category=parent.category, priority=parent.priority,
                 due_at=due, estimated_minutes=int(st.get("estimated_minutes") or 30),
                 energy=st.get("energy") or parent.energy or "normal", parent_id=parent.id,
                 recurring_event_id=parent.recurring_event_id, goal_id=parent.goal_id, source=ctx.channel)
        s.add(t)
        s.flush()
        journal.created(s, ctx, t, f"زیرکار «{t.title}»")
        created.append(t)
    journal.before_update(s, ctx, parent)
    if parent.status == "todo":
        parent.status = "doing"
    planner.invalidate(s)
    from .. import followups

    for t in created:
        followups.ensure_task_followups(s, t, prefs.all_prefs(s), timeutil.now())
    return {"project": serialize.task(s, parent), "subtasks": [serialize.task(s, t) for t in created]}


@tool(
    "plan_exam_prep",
    "Backward planning for exams (e.g. final exams): for each exam creates the exam event (if missing), a study "
    "project and study sessions per topic with intermediate deadlines spread from now to the exam, ending with a "
    "final review. The planner then fits them around classes.",
    {
        "exams": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "course name"},
                    "date": {"type": "string", "description": "exam start. " + DT_HELP},
                    "duration_minutes": {"type": "integer"},
                    "study_hours": {"type": "number", "description": "total study time needed (your estimate)"},
                    "topics": {"type": "array", "items": {"type": "string"}, "description": "chapters / topics"},
                    "location": {"type": "string"},
                },
                "required": ["title", "date", "study_hours"],
            },
        }
    },
    ["exams"],
    changes=("tasks", "schedule", "followups"),
)
def plan_exam_prep(s: Session, ctx: Ctx, exams: list[dict[str, Any]]):
    now = timeutil.now()
    out = []
    for ex in exams:
        at = timeutil.parse_datetime(ex["date"], default_time=timeutil.parse_time("09:00"))
        if at <= now:
            out.append({"title": ex["title"], "error": "تاریخ امتحان گذشته است"})
            continue
        title = str(ex["title"]).strip()
        ev = s.query(Event).filter(Event.kind == "exam", Event.title.contains(title),
                                   Event.start_at >= at - timedelta(hours=12), Event.start_at <= at + timedelta(hours=12)).first()
        if ev is None:
            ev = Event(title=f"امتحان {title}", kind="exam", start_at=at,
                       end_at=at + timedelta(minutes=int(ex.get("duration_minutes") or 120)), location=ex.get("location", ""))
            s.add(ev)
            s.flush()
            journal.created(s, ctx, ev, f"امتحان «{title}»")
        total = max(30, int(float(ex.get("study_hours") or 4) * 60))
        topics = [t for t in (ex.get("topics") or []) if str(t).strip()] or ["مطالعه بخش اول", "مطالعه بخش دوم", "حل تمرین و نمونه سؤال"]
        review_min = max(30, int(total * 0.2))
        per_topic = max(30, (total - review_min) // len(topics))
        project = Task(title=f"آمادگی امتحان {title}", category="university", priority=1, due_at=at,
                       estimated_minutes=total, energy="high", source=ctx.channel)
        s.add(project)
        s.flush()
        journal.created(s, ctx, project, f"آمادگی «{title}»")
        review_due = at - timedelta(hours=2)
        study_end = at - timedelta(days=1) if at - now > timedelta(days=2) else at - timedelta(hours=4)
        span = max(study_end - now, timedelta(hours=1))
        subs = []
        for i, topic in enumerate(topics):
            due = now + span * (i + 1) / len(topics)
            t = Task(title=f"{title}: {topic}", category="university", priority=1, due_at=due.replace(second=0, microsecond=0),
                     estimated_minutes=per_topic, energy="high", parent_id=project.id, source=ctx.channel)
            s.add(t)
            subs.append(t)
        rv = Task(title=f"{title}: مرور نهایی و نمونه سؤال", category="university", priority=1, due_at=review_due,
                  estimated_minutes=review_min, energy="high", parent_id=project.id, source=ctx.channel)
        s.add(rv)
        subs.append(rv)
        s.flush()
        for t in subs:
            journal.created(s, ctx, t)
        out.append({
            "exam": serialize.event(ev),
            "project_id": project.id,
            "sessions": [{"title": t.title, "due": timeutil.iso(t.due_at), "minutes": t.estimated_minutes} for t in subs],
        })
    planner.invalidate(s)
    return {"exams": out, "note": "Study sessions are tasks with intermediate deadlines; the plan is refreshed automatically."}


@tool(
    "move_block",
    "Move a planned work block to another time (it stays where the user put it when re-planning).",
    {"block_id": {"type": "integer"}, "start": {"type": "string", "description": DT_HELP},
     "duration_minutes": {"type": "integer"}},
    ["block_id", "start"],
    changes=("schedule", "followups"),
)
def move_block(s: Session, ctx: Ctx, block_id: int, start: str, duration_minutes: int | None = None):
    b = _get(s, PlannedBlock, block_id, "بلوک")
    journal.before_update(s, ctx, b, "جابه‌جایی بلوک")
    length = timedelta(minutes=int(duration_minutes)) if duration_minutes else b.end_at - b.start_at
    b.start_at = timeutil.parse_datetime(start)
    b.end_at = b.start_at + length
    b.locked = True
    b.status = "planned"
    s.flush()
    return {"block": {"id": b.id, "start": timeutil.iso(b.start_at), "end": timeutil.iso(b.end_at)}}


@tool(
    "rescue_plan",
    "Analyse what to do when the workload doesn't fit before deadlines: which tasks are at risk and by how much, "
    "what could be postponed (and how much time that frees), extra daily time needed, and which deadlines are worth "
    "asking an extension for (professor / manager). Use when the user feels behind or plan warnings appear.",
    {},
)
def rescue_plan(s: Session, ctx: Ctx):
    return rescue.analyze(s)


# ------------------------------------------------------------------ undo
@tool(
    "undo_last",
    "Undo the changes you made in the previous turn(s) (when the user says برگردون / undo / اشتباه شد).",
    {"turns": {"type": "integer", "description": "How many previous turns with changes to undo (default 1)."}},
    changes=("tasks", "schedule", "followups", "memories", "goals", "settings", "habits"),
)
def undo_last(s: Session, ctx: Ctx, turns: int = 1):
    q = (
        s.query(Journal.turn_id)
        .filter(Journal.undone.is_(False), Journal.turn_id != (ctx.turn_id or ""))
        .order_by(Journal.id.desc())
    )
    seen: list[str] = []
    for (tid,) in q.all():
        if tid not in seen:
            seen.append(tid)
        if len(seen) >= max(1, int(turns)):
            break
    s.commit()
    reverted: list[str] = []
    for tid in seen:
        reverted += journal.undo(tid)
    return {"undone_turns": len(seen), "reverted": reverted or ["(چیزی برای برگرداندن نبود)"]}


# ------------------------------------------------------------------ habits
@tool(
    "add_habit",
    "Start tracking a habit (exercise, reading, sleep hours, meditation...). period daily or weekly; target per "
    "period in the unit. Optional reminder_time HH:MM for a daily nudge if not done yet.",
    {
        "title": {"type": "string"},
        "unit": {"type": "string", "enum": ["times", "minutes", "hours"]},
        "target": {"type": "number"},
        "period": {"type": "string", "enum": ["daily", "weekly"]},
        "reminder_time": {"type": "string", "description": "HH:MM"},
        "category": {"type": "string", "enum": CATEGORIES},
    },
    ["title"],
    changes=("habits", "followups"),
)
def add_habit(s: Session, ctx: Ctx, title: str, unit: str = "times", target: float = 1, period: str = "daily",
              reminder_time: str | None = None, category: str = "health"):
    h = Habit(title=title.strip(), unit=unit if unit in ("times", "minutes", "hours") else "times", target=float(target or 1),
              period=period if period in ("daily", "weekly") else "daily",
              reminder_time=timeutil.parse_time(reminder_time).strftime("%H:%M") if reminder_time else None,
              category=_cat(category, "health"))
    s.add(h)
    s.flush()
    journal.created(s, ctx, h, f"عادت «{h.title}»")
    return {"habit": habits.habit_stats(s, h)}


@tool(
    "log_habit",
    "Record a habit for a day (e.g. 'ran 30 min', 'slept 6 hours', 'read 20 pages' → value in the habit's unit).",
    {"habit_id": {"type": "integer"}, "value": {"type": "number"}, "day": {"type": "string", "description": "YYYY-MM-DD, default today"},
     "note": {"type": "string"}},
    ["habit_id"],
    changes=("habits", "stats"),
)
def log_habit(s: Session, ctx: Ctx, habit_id: int, value: float = 1, day: str | None = None, note: str = ""):
    h = _get(s, Habit, habit_id, "عادت")
    lg = HabitLog(habit_id=h.id, day=timeutil.parse_date(day) if day else timeutil.today(), value=float(value), note=note)
    s.add(lg)
    s.flush()
    journal.created(s, ctx, lg, f"ثبت «{h.title}»")
    return {"habit": habits.habit_stats(s, h)}


@tool("list_habits", "Habits with current/best streaks and the last 7 days.", {})
def list_habits(s: Session, ctx: Ctx):
    return {"habits": habits.all_habits(s, active_only=False)}


@tool(
    "update_habit",
    "Change or pause (active=false) a habit.",
    {"habit_id": {"type": "integer"}, "title": {"type": "string"}, "target": {"type": "number"},
     "unit": {"type": "string", "enum": ["times", "minutes", "hours"]}, "period": {"type": "string", "enum": ["daily", "weekly"]},
     "reminder_time": {"type": "string"}, "active": {"type": "boolean"}},
    ["habit_id"],
    changes=("habits", "followups"),
)
def update_habit(s: Session, ctx: Ctx, habit_id: int, **data):
    h = _get(s, Habit, habit_id, "عادت")
    journal.before_update(s, ctx, h, f"ویرایش «{h.title}»")
    for key in ("title", "target", "unit", "period", "active"):
        if key in data:
            setattr(h, key, data[key])
    if "reminder_time" in data:
        h.reminder_time = timeutil.parse_time(data["reminder_time"]).strftime("%H:%M") if data["reminder_time"] else None
    s.flush()
    return {"habit": habits.habit_stats(s, h)}


@tool("delete_habit", "Delete a habit and its history (asks for confirmation first; prefer active=false).",
      {"habit_id": {"type": "integer"}, "confirmed": _CONFIRM}, ["habit_id"], changes=("habits",))
def delete_habit(s: Session, ctx: Ctx, habit_id: int, confirmed: bool = False):
    h = _get(s, Habit, habit_id, "عادت")
    guard = journal.confirm_guard(ctx, f"habit:{h.id}", f"عادت «{h.title}» و همه سابقه‌اش", confirmed)
    if guard:
        return guard
    for lg in s.query(HabitLog).filter(HabitLog.habit_id == h.id).all():
        journal.before_delete(s, ctx, lg)
    journal.before_delete(s, ctx, h, f"حذف عادت «{h.title}»")
    s.delete(h)
    return {"deleted": habit_id}


# ------------------------------------------------------------------ commitments
@tool(
    "add_commitment",
    "Record a daily commitment/promise the user makes (\"today I'll do 2h of writing\"). Link to a task or category "
    "and give target_minutes when measurable, so it can be checked automatically in the evening.",
    {"text": {"type": "string"}, "target_minutes": {"type": "integer"}, "task_id": {"type": "integer"},
     "category": {"type": "string", "enum": CATEGORIES}, "day": {"type": "string", "description": "YYYY-MM-DD, default today"}},
    ["text"],
    changes=("commitments",),
)
def add_commitment(s: Session, ctx: Ctx, text: str, target_minutes: int | None = None, task_id: int | None = None,
                   category: str | None = None, day: str | None = None):
    c = Commitment(day=timeutil.parse_date(day) if day else timeutil.today(), text=text.strip(),
                   target_minutes=target_minutes, task_id=task_id, category=category)
    s.add(c)
    s.flush()
    journal.created(s, ctx, c, f"قول «{c.text[:40]}»")
    return {"commitment": habits.commitment_progress(s, c)}


@tool(
    "update_commitment",
    "Set the outcome of a commitment: done | partial | missed (with an optional note).",
    {"commitment_id": {"type": "integer"}, "status": {"type": "string", "enum": ["pending", "done", "partial", "missed"]},
     "note": {"type": "string"}},
    ["commitment_id", "status"],
    changes=("commitments",),
)
def update_commitment(s: Session, ctx: Ctx, commitment_id: int, status: str, note: str = ""):
    c = _get(s, Commitment, commitment_id, "قول")
    journal.before_update(s, ctx, c)
    c.status = status
    if note:
        c.note = note
    s.flush()
    return {"commitment": habits.commitment_progress(s, c)}


@tool("list_commitments", "Commitments of a day with automatic progress from logged time.",
      {"day": {"type": "string", "description": "YYYY-MM-DD, default today"}})
def list_commitments(s: Session, ctx: Ctx, day: str | None = None):
    d = timeutil.parse_date(day) if day else timeutil.today()
    return {"day": d.isoformat(), "commitments": habits.auto_evaluate(s, d)}


# ------------------------------------------------------------------ IELTS coach
@tool(
    "grade_ielts_writing",
    "Grade an IELTS essay (Task 1 or Task 2) with the official criteria; returns band per criterion, overall, "
    "corrections and vocabulary, and saves the score. Use when the user sends an essay/writing.",
    {"essay": {"type": "string"}, "task_type": {"type": "string", "enum": ["task1", "task2"]},
     "question": {"type": "string"}},
    ["essay"],
    changes=("ielts",),
)
async def grade_ielts_writing(ctx: Ctx, essay: str, task_type: str = "task2", question: str = ""):
    data = await ielts.grade_writing(essay, task_type, question)
    with session() as s:
        sc = ielts.save_score(s, "writing", data.get("overall", 0), "writing_grade",
                              details={k: v for k, v in data.items() if k in ("criteria", "word_count")},
                              note=question[:200])
        journal.created(s, ctx, sc, "نمره رایتینگ")
    return {**data, "saved_score_id": sc.id,
            "note": "Show bands per criterion, the 2-3 most important improvements and corrections. Offer to add the vocabulary to the review deck (add_vocab)."}


@tool(
    "start_speaking_practice",
    "Start an IELTS speaking practice: you choose a question / Part 2 cue card, this tool stores it and switches "
    "voice transcription to English for 30 minutes. Then ask the user to answer with a voice message.",
    {"part": {"type": "integer", "enum": [1, 2, 3]}, "question": {"type": "string"}},
    ["question"],
)
def start_speaking_practice(s: Session, ctx: Ctx, question: str, part: int = 2):
    until = timeutil.now() + timedelta(minutes=30)
    prefs.set("speaking_practice", {"question": question, "part": int(part), "until": timeutil.iso(until)}, s=s)
    return {"ok": True, "until": timeutil.iso(until)}


@tool(
    "grade_ielts_speaking",
    "Grade the user's spoken answer (from the voice transcript) for the current speaking practice question.",
    {"transcript": {"type": "string"}, "question": {"type": "string"}, "part": {"type": "integer", "enum": [1, 2, 3]}},
    ["transcript"],
    changes=("ielts",),
)
async def grade_ielts_speaking(ctx: Ctx, transcript: str, question: str = "", part: int | None = None):
    practice = prefs.get("speaking_practice") or {}
    question = question or practice.get("question", "")
    part = int(part or practice.get("part") or 2)
    data = await ielts.grade_speaking(transcript, question, part)
    with session() as s:
        sc = ielts.save_score(s, "speaking", data.get("overall", 0), "speaking_grade",
                              details={"criteria": data.get("criteria"), "part": part}, note=question[:200])
        journal.created(s, ctx, sc, "نمره اسپیکینگ")
        prefs.set("speaking_practice", None, s=s)
    return {**data, "saved_score_id": sc.id}


@tool(
    "log_ielts_score",
    "Save an IELTS score the user reports (mock test, practice test, official result).",
    {"skill": {"type": "string", "enum": ielts.SKILLS}, "band": {"type": "number"},
     "source": {"type": "string", "enum": ["mock", "official", "practice"]}, "day": {"type": "string"},
     "note": {"type": "string"}},
    ["skill", "band"],
    changes=("ielts",),
)
def log_ielts_score(s: Session, ctx: Ctx, skill: str, band: float, source: str = "mock", day: str | None = None,
                    note: str = ""):
    sc = ielts.save_score(s, skill, band, source, day=timeutil.parse_date(day) if day else None, note=note)
    journal.created(s, ctx, sc, f"نمره {ielts.SKILL_FA.get(skill, skill)}")
    return {"saved": sc.id, "progress": ielts.progress(s)}


@tool("ielts_progress", "IELTS progress: scores per skill over time, gap to target band, days to exam, vocab stats.", {})
def ielts_progress(s: Session, ctx: Ctx):
    return ielts.progress(s)


@tool(
    "add_vocab",
    "Add English words/phrases to the spaced-repetition review deck (with Persian meaning and an example).",
    {"words": {"type": "array", "items": {"type": "object", "properties": {
        "word": {"type": "string"}, "meaning": {"type": "string"}, "example": {"type": "string"}}, "required": ["word"]}}},
    ["words"],
    changes=("ielts",),
)
def add_vocab(s: Session, ctx: Ctx, words: list[dict[str, Any]]):
    added = ielts.add_words(s, words)
    for w in added:
        journal.created(s, ctx, w)
    return {"added": [w.word for w in added], "stats": ielts.vocab_stats(s)}


@tool("vocab_due", "Words due for review today (Leitner boxes).", {"limit": {"type": "integer"}})
def vocab_due(s: Session, ctx: Ctx, limit: int = 8):
    return {"words": [ielts.word_dict(w) for w in ielts.due_words(s, int(limit))]}


@tool(
    "vocab_review",
    "Record the result of reviewing a word (after the user answered its meaning): correct moves it to a later box.",
    {"word_id": {"type": "integer"}, "correct": {"type": "boolean"}},
    ["word_id", "correct"],
    changes=("ielts",),
)
def vocab_review(s: Session, ctx: Ctx, word_id: int, correct: bool):
    w = _get(s, VocabWord, word_id, "لغت")
    journal.before_update(s, ctx, w)
    ielts.review(s, w, bool(correct))
    return {"word": ielts.word_dict(w)}


# ------------------------------------------------------------------ insights
@tool(
    "get_insights",
    "What has been learned from the user's data: estimation bias per class/category, productive peak hours, "
    "which reminders get ignored, points/level and this week's score.",
    {},
)
def get_insights(s: Session, ctx: Ctx):
    factors = insights.estimation_factors(s)
    peaks = insights.peak_hours(s)
    rs = insights.response_stats(s)
    return {
        "estimation": factors,
        "estimation_text": insights.estimation_lines(factors),
        "peak_hours": peaks["hours"],
        "peak_hours_learned": peaks["learned"],
        "reminders": rs,
        "reminders_text": insights.reminder_lines(rs),
        "gamification": gamify.summary(s),
    }


# ------------------------------------------------------------------ projects
from .. import projects as projects_mod  # noqa: E402
from ..db import Contact, Project  # noqa: E402


@tool(
    "add_project",
    "Register a project the user works on, with ALL the nicknames they use for it (e.g. name 'tahami supercars', "
    "aliases ['پروژه ماشین', 'ماشین', 'tahami']). Then link tasks/time with project='<name or nickname>'.",
    {
        "name": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string", "enum": CATEGORIES, "description": "work for company/freelance projects"},
        "client": {"type": "string", "description": "who the work is delivered to (کارفرما), if known"},
        "description": {"type": "string"},
    },
    ["name"],
    changes=("projects", "tasks"),
)
def add_project(s: Session, ctx: Ctx, name: str, aliases: list[str] | None = None, category: str = "work",
                client: str = "", description: str = ""):
    existing = projects_mod.resolve(s, name)
    if existing is not None and projects_mod.norm(existing.name) == projects_mod.norm(name):
        journal.before_update(s, ctx, existing)
        existing.aliases = sorted({*(existing.aliases or []), *(aliases or [])})
        if client:
            existing.client = client
        if description:
            existing.description = description
        s.flush()
        return {"project": projects_mod.project_dict(s, existing), "note": "already existed; nicknames merged"}
    p = Project(name=name.strip(), aliases=[a.strip() for a in aliases or [] if a.strip()], category=_cat(category, "work"),
                client=client, description=description)
    s.add(p)
    s.flush()
    journal.created(s, ctx, p, f"پروژه «{p.name}»")
    return {"project": projects_mod.project_dict(s, p)}


@tool(
    "update_project",
    "Change a project (find it by name, nickname or id): rename, add nicknames, client, status active|paused|done.",
    {
        "project": {"type": "string", "description": "name / nickname / id"},
        "name": {"type": "string"},
        "add_aliases": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string", "enum": CATEGORIES},
        "client": {"type": "string"},
        "description": {"type": "string"},
        "status": {"type": "string", "enum": ["active", "paused", "done"]},
    },
    ["project"],
    changes=("projects", "tasks"),
)
def update_project(s: Session, ctx: Ctx, project: str, **data):
    p = projects_mod.resolve(s, project)
    if p is None:
        return {"error": f"project '{project}' not found", "known": [x.name for x in s.query(Project).all()]}
    journal.before_update(s, ctx, p, f"پروژه «{p.name}»")
    for key in ("name", "client", "description", "status"):
        if key in data:
            setattr(p, key, data[key])
    if "category" in data:
        p.category = _cat(data["category"], p.category)
    if data.get("add_aliases"):
        p.aliases = sorted({*(p.aliases or []), *(a.strip() for a in data["add_aliases"] if a.strip())})
    s.flush()
    return {"project": projects_mod.project_dict(s, p)}


@tool("list_projects", "Projects with nicknames, open tasks, time spent this week and next deadline.", {})
def list_projects(s: Session, ctx: Ctx):
    return {"projects": [projects_mod.project_dict(s, p) for p in s.query(Project).order_by(Project.id).all()]}


# ------------------------------------------------------------------ supervisor search / contacts
_CONTACT_PROPS = {
    "name": {"type": "string", "description": "e.g. 'Prof. Jane Smith'"},
    "organization": {"type": "string", "description": "university / lab / company"},
    "field": {"type": "string", "description": "research area / why they fit"},
    "email": {"type": "string"},
    "url": {"type": "string"},
    "status": {"type": "string", "enum": projects_mod.CONTACT_STATUS},
    "next_followup": {"type": "string", "description": DT_HELP},
    "notes": {"type": "string"},
}


@tool(
    "add_contact",
    "Track a professor / potential supervisor (or other person) in the application pipeline. Status: to_contact, "
    "contacted (email sent → a follow-up is scheduled automatically), replied, interview, positive, rejected, no_reply.",
    _CONTACT_PROPS,
    ["name"],
    changes=("contacts", "followups"),
)
def add_contact(s: Session, ctx: Ctx, name: str, status: str = "to_contact", next_followup: str | None = None, **data):
    c = Contact(name=name.strip(), **{k: v for k, v in data.items() if k in ("organization", "field", "email", "url", "notes")})
    projects_mod.apply_status(c, status)
    if next_followup:
        c.next_followup_at = timeutil.parse_datetime(next_followup, default_time=timeutil.parse_time("10:00"))
    s.add(c)
    s.flush()
    journal.created(s, ctx, c, f"استاد «{c.name}»")
    return {"contact": projects_mod.contact_dict(c)}


@tool(
    "update_contact",
    "Update a contact found by name or id: new status (e.g. 'contacted' after sending the email, 'replied'), "
    "add a note, set the next follow-up date.",
    {"contact": {"type": "string", "description": "name or id"}, **_CONTACT_PROPS, "add_note": {"type": "string"},
     "contacted_at": {"type": "string", "description": "when the email was sent, if not now"}},
    ["contact"],
    changes=("contacts", "followups"),
)
def update_contact(s: Session, ctx: Ctx, contact: str, **data):
    c = projects_mod.resolve_contact(s, contact)
    if c is None:
        return {"error": f"contact '{contact}' not found", "known": [x.name for x in s.query(Contact).all()]}
    journal.before_update(s, ctx, c, f"استاد «{c.name}»")
    for key in ("name", "organization", "field", "email", "url", "notes"):
        if key in data:
            setattr(c, key, data[key])
    if data.get("status"):
        when = timeutil.parse_datetime(data["contacted_at"]) if data.get("contacted_at") else None
        projects_mod.apply_status(c, data["status"], when)
    if data.get("next_followup"):
        c.next_followup_at = timeutil.parse_datetime(data["next_followup"], default_time=timeutil.parse_time("10:00"))
    if data.get("add_note"):
        c.notes = (c.notes + "\n" if c.notes else "") + f"[{timeutil.jalali_str(timeutil.now())}] {data['add_note']}"
    s.flush()
    return {"contact": projects_mod.contact_dict(c)}


@tool("list_contacts", "The supervisor-search pipeline (all contacts with status, last contact, next follow-up).",
      {"status": {"type": "string", "enum": projects_mod.CONTACT_STATUS}})
def list_contacts(s: Session, ctx: Ctx, status: str | None = None):
    data = projects_mod.pipeline(s)
    if status:
        data["contacts"] = [c for c in data["contacts"] if c["status"] == status]
    return data
