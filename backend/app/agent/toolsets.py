"""Which tools (and which instructions) the model gets in a turn.

Sending all ~60 tool definitions on every model call was more than half of each request. Now every call gets the
core tools, plus the groups the turn is likely to need: picked from keywords in the user's message and the last
assistant message, the kind of proactive message being answered, attachments, and groups used in the previous
turns. If the model still needs something else it calls `load_tools`, and the group is added for the rest of the turn.
Each group carries its own instructions, which are only added to the prompt while the group is loaded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CORE = [
    "get_agenda", "list_tasks", "add_task", "update_task", "complete_task", "delete_task",
    "add_event", "check_availability", "log_work", "start_focus", "stop_focus",
    "remember", "schedule_followup", "undo_last", "load_tools",
]


@dataclass(frozen=True)
class Group:
    title: str
    tools: tuple[str, ...]
    keywords: str
    guide: str = ""


GROUPS: dict[str, Group] = {
    "schedule": Group(
        "classes, timetable, edit/delete events, move blocks",
        ("add_recurring_events", "update_recurring_event", "delete_recurring_event", "list_recurring_events",
         "update_event", "delete_event", "move_block"),
        r"کلاس|ترم|دانشگاه|برنامه ?(ی )?(هفتگی|کلاسی|درسی)|تایم ?تیبل|timetable|جلسه|رویداد|قرار|میتینگ|meeting|"
        r"کنسل|لغو|جابه ?‌?جا|بلوک|تقویم|زوج|فرد|class|event",
        """- Timetable images: an image description/extraction may be attached to the user's message. If it is a class
  timetable, add all classes with add_recurring_events in one call (kind=university_class unless obvious otherwise;
  follow the odd/even-week (فرد/زوج) info if present), then show a compact summary and ask the user to correct
  anything wrong. If odd/even weeks are used and you don't know which week this is, ask "این هفته فرده یا زوج؟"
  and save it with update_preferences(current_week_parity).""",
    ),
    "planning": Group(
        "plan the week, split big tasks, exam prep, rescue plan",
        ("plan_schedule", "break_down_task", "plan_exam_prep", "rescue_plan", "move_block"),
        r"برنامه ?‌?ریزی|برنامه ?(ام|مو|م) |برنامه بریز|بچین|عقب|نمی ?‌?رسم|نمیرسم|امتحان|میان ?‌?ترم|فاینال|"
        r"بشکن|مرحله|زیرکار|فشرده|وقت (کم|ندارم)|plan|exam|deadline",
        """- Planning: use plan_schedule and present the plan grouped by day, concise. Warn clearly when something won't
  fit before its deadline, and suggest what to drop/move.
- Big tasks (more than ~3 hours or several days): split them with break_down_task into milestones with intermediate
  deadlines. Exams: use plan_exam_prep for backward planning from the exam date.
- When the plan has warnings or the user feels behind, use rescue_plan and help them choose: postpone, more time per
  day, ask for an extension (draft the message), or shrink the scope.""",
    ),
    "followups": Group(
        "list/cancel reminders",
        ("list_followups", "cancel_followup"),
        r"یادآوری|یادم بنداز|ریمایندر|remind|پیگیری|نپرس|دیگه نگو",
    ),
    "goals": Group(
        "long-term goals",
        ("add_goal", "update_goal"),
        r"هدف|goal|اهداف",
    ),
    "reports": Group(
        "weekly report, productivity stats",
        ("weekly_report", "get_stats", "get_insights"),
        r"گزارش|آمار|چقدر کار|بازدهی|ساعت مفید|عملکرد|report|stats|insight",
        """- Weekly report: use weekly_report; it creates a professional work report for the employer (or for one
  project's client with project=...) and a personal review. Show both.""",
    ),
    "settings": Group(
        "preferences (name, sleep, work hours, week parity…), forget a memory",
        ("update_preferences", "forget"),
        r"تنظیم|ساعت (خواب|بیدار)|بیدار می|می ?‌?خوابم|اسمم|صدام کن|setting|ترجیح|هفته (فرد|زوج)|فراموش کن|یادت نباشه|"
        r"کمتر (پیام|پیگیری)|بیشتر پیگیر",
    ),
    "habits": Group(
        "habits, daily commitments",
        ("add_habit", "log_habit", "list_habits", "update_habit", "delete_habit",
         "add_commitment", "update_commitment", "list_commitments"),
        r"عادت|ورزش|دویدم|باشگاه|خوابیدم|ساعت خواب|کتاب خوندم|قول|تعهد|habit|streak|ورزش",
        """- Habits: when the user mentions doing a tracked habit ("امروز دویدم", "دیشب ۶ ساعت خوابیدم") log it with
  log_habit; when they want to build a habit, add_habit (suggest a reminder time). Celebrate streaks.
- Daily commitments: when the user promises something for today, save it with add_commitment (measurable:
  target_minutes + task/category). In the evening review, record outcomes with update_commitment.""",
    ),
    "ielts": Group(
        "IELTS writing/speaking grading, scores, vocabulary",
        ("grade_ielts_writing", "start_speaking_practice", "grade_ielts_speaking", "log_ielts_score",
         "ielts_progress", "add_vocab", "vocab_due", "vocab_review"),
        r"آیلتس|ielts|رایتینگ|writing|اسپیکینگ|speaking|essay|اسی|لغت|واژه|vocab|band|لیسنینگ|ریدینگ|listening|reading|"
        r"task ?[12]|cue card",
        """- IELTS coach: an English essay (typed, or transcribed from a photo) → grade_ielts_writing and give focused
  feedback (bands per criterion, top improvements, corrections) and offer to add new vocabulary with add_vocab.
  Speaking practice → pick a realistic question/cue card, call start_speaking_practice, ask for a voice answer, then
  grade_ielts_speaking on the transcript. Reported mock scores → log_ielts_score. Quiz answers → vocab_review per word.""",
    ),
    "places": Group(
        "places, travel times, getting-ready time",
        ("set_place", "set_travel_time", "list_places", "update_preferences"),
        r"خونه|خانه|شرکت|محل کار|دانشگاه|آموزشگاه|باشگاه|مسیر|ترافیک|برسم|رسیدم|راه بیفت|طول (می ?‌?)?کشه|"
        r"حاضر (شدن|بشم|میشم)|آماده (شدن|بشم)|رفت ?و ?آمد|فاصله|مترو|تاکسی|اسنپ|ماشین ?(خودم|شخصی)|travel|commute",
        """- Places & travel: register the places the user goes to with set_place (home is kind=home) and the
  door-to-door travel time between them with set_travel_time (both ways unless they differ). When the user reports
  how long a trip really took ("امروز ۵۰ دقیقه طول کشید برسم"), call set_travel_time with observed=true — it is
  averaged with what was known. Getting-ready time before leaving home → update_preferences(prep_minutes).
  Classes/events whose location matches a place automatically get travel time in the plan and a "time to get
  ready / leave" reminder.""",
    ),
    "projects": Group(
        "projects, professors / supervisor search",
        ("add_project", "update_project", "list_projects", "add_contact", "update_contact", "list_contacts"),
        r"پروژه|project|استاد|پروفسور|professor|prof\.?|dr\.?|اپلای|apply|ایمیل|email|سوپروایزر|supervisor|"
        r"کارفرما|مشتری|client|دانشگاه .* (خارج|کانادا|آلمان|آمریکا)|ریسرچ|research|پوزیشن|position",
        """- Projects: when the user mentions a new project, register it once with add_project including every nickname
  ("tahami supercars" = «پروژه ماشین»), then link tasks and logged time with project="<name or nickname>".
- Applying / supervisor search (پیدا کردن استاد): track each professor with add_contact (university, research field,
  email). When the user says they emailed someone, update_contact(status="contacted") — a follow-up is then scheduled
  automatically. Help draft tailored cold emails and follow-ups in English, and keep the pipeline moving.""",
    ),
}

# proactive message kinds → the groups an answer to them will probably need
KIND_GROUPS = {
    "post_class": ("schedule",),
    "daily_brief": ("habits", "planning"),
    "daily_review": ("habits", "planning"),
    "weekly_planning": ("planning", "schedule"),
    "rescue": ("planning",),
    "habit_reminder": ("habits",),
    "vocab_review": ("ielts",),
    "contact_followup": ("projects",),
    "block_start": ("planning",),
    "block_end": ("planning",),
    "weekly_report": ("reports",),
}

_COMPILED = {name: re.compile(g.keywords, re.I) for name, g in GROUPS.items()}


def select(texts: list[str], kinds: list[str] = (), sticky: list[str] = (), attachments: list[str] = ()) -> list[str]:
    """Groups to load for this turn, in a stable order (so the request prefix stays cacheable)."""
    chosen: set[str] = {g for g in sticky if g in GROUPS}
    blob = "\n".join(t for t in texts if t)
    chosen |= {name for name, rx in _COMPILED.items() if rx.search(blob)}
    for k in kinds:
        chosen.update(KIND_GROUPS.get(k, ()))
    if "image" in attachments:
        chosen |= {"schedule", "ielts"}
    if "document" in attachments:
        chosen |= {"schedule", "planning"}
    return [name for name in GROUPS if name in chosen]


def tool_names(groups: list[str] | set[str]) -> list[str]:
    names = list(CORE)
    for name in GROUPS:
        if name in groups:
            names += [t for t in GROUPS[name].tools if t not in names]
    return names


def guides(groups: list[str] | set[str]) -> str:
    return "\n".join(GROUPS[g].guide for g in GROUPS if g in groups and GROUPS[g].guide)


def catalog() -> str:
    """One line per group, for the load_tools description."""
    return "; ".join(f"{name} = {g.title}" for name, g in GROUPS.items())
