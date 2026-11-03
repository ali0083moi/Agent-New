"""Compare GapGPT models on realistic Peygir scenarios (tool use, Persian dates, vision, IELTS...).

Usage (from backend/, with GAPGPT_API_KEY in .env):

    python -m app.evals --models gpt-4.1,claude-sonnet-5-5,gemini-3.5-flash
    python -m app.evals --models gpt-4.1-mini --scenarios homework,jalali --out evals-report.md

Every scenario runs on a fresh temporary database with a fixed clock (Monday 2026-10-19 10:00, Tehran).
The report lists pass rate, latency and estimated cost per model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func

from . import db, followups, prefs, timeutil
from .config import get_settings
from .db import (Event, Followup, Habit, HabitLog, IeltsScore, LlmUsage, Message, PlannedBlock, Project,
                 RecurringEvent, Task, session)

FIXED_NOW = datetime(2026, 10, 19, 10, 0)
EVAL_DIR = Path(__file__).resolve().parents[1] / "evals"

Check = tuple[str, bool]


@dataclass
class Scenario:
    name: str
    title: str
    turns: list[str]
    checks: Callable[["Run"], list[Check]]
    seed: Callable[[], None] = lambda: None
    image: str | None = None


@dataclass
class Run:
    replies: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    metas: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------- helpers
def _persian(text: str) -> bool:
    fa = sum(1 for ch in text if "؀" <= ch <= "ۿ")
    return fa >= max(5, len(text) * 0.3)


LEAKS = ("[actions done", "[proactive", "SYSTEM TRIGGER", '{"tool_names"', "no tools exposed", "IntegrityError",
         "sqlalche", "Traceback")


def _clean(text: str) -> bool:
    """No internal text: markers, raw errors or tool names (real chats showed `plan_schedule`, `start_focus`...)."""
    from .agent import tools

    if any(x in text for x in LEAKS):
        return False
    return not any(re.search(rf"\b{name}\b", text) for name in tools.REGISTRY if "_" in name)


def _seed_ielts_class() -> None:
    with session() as s:
        s.add(RecurringEvent(title="کلاس آیلتس", kind="language_class", weekdays=["tue", "thu"],
                             start_time="18:00", end_time="20:00", followup=True))


def _seed_busy_week() -> None:
    _seed_ielts_class()
    with session() as s:
        s.add(Task(title="پروژه پایگاه داده", category="university", priority=1, estimated_minutes=720,
                   due_at=datetime(2026, 10, 23, 23, 59)))
        s.add(Task(title="گزارش کاری اسپرینت", category="work", priority=1, estimated_minutes=300,
                   due_at=datetime(2026, 10, 22, 17, 0)))


def _seed_book_task() -> None:
    with session() as s:
        s.add(Task(title="خرید کتاب کمبریج ۱۸", category="language", estimated_minutes=30))


def _seed_recent_task() -> None:
    """A task the user added a minute ago through the assistant (so it is in the 'recently added' list)."""
    from .agent import tools

    with session() as s:
        tools.REGISTRY["add_task"].fn(s, ctx=tools.Ctx(turn_id="seed"), title="ارسال گزارش پروژه ماشین",
                                      due="2026-10-22 18:00", estimated_minutes=120, category="work")


def _seed_week_tasks() -> None:
    with session() as s:
        for title, day in (("تمرین سری ۳ آمار", 20), ("خلاصه فصل ۴ مدیریت", 21), ("ویدیوی دوره React", 22)):
            s.add(Task(title=title, category="university", estimated_minutes=60, due_at=datetime(2026, 10, day, 20, 0)))


def _seed_habit() -> None:
    with session() as s:
        s.add(Habit(title="ورزش", unit="minutes", target=30))


ESSAY = (
    "Some people believe that technology has made our lives more complicated, while others think it has made life "
    "easier. In my opinion, although technology brings some new problems, its benefits are much greater. "
    "Firstly, technology saves time. For example, online banking allows people to pay bills in minutes instead of "
    "waiting in long queues. Moreover, communication has become easier because we can contact our family who live "
    "abroad with video calls. On the other hand, some people argue that technology makes people addicted to their "
    "phones and they spend less time with their friends. This is true for many teenagers, who use social media for "
    "hours every day. However, this problem can be solved by education and better habits. In conclusion, I believe "
    "technology makes our life easier, but we should use it wisely."
)

EXPECTED_CLASSES = [
    ("ریاضی", {"sat", "mon"}, "08:00", "10:00", "all"),
    ("فیزیک ۱", {"sat"}, "14:00", "16:00", "odd"),
    ("برنامه", {"sun"}, "10:00", "12:00", "all"),
    ("زبان", {"mon"}, "16:00", "18:00", "all"),
    ("مدار", {"tue"}, "10:00", "12:00", "all"),
    ("آز", {"wed"}, "14:00", "16:00", "even"),
]


def _timetable_checks(run: Run) -> list[Check]:
    with session() as s:
        recs = s.query(RecurringEvent).all()
    out = []
    for key, days, st, en, parity in EXPECTED_CLASSES:
        found = [r for r in recs if key in r.title and set(r.weekdays) & days]
        ok_days = bool(found) and set().union(*[set(r.weekdays) for r in found]) >= days
        ok_time = any(r.start_time == st and r.end_time == en for r in found)
        ok_parity = parity == "all" or any(r.week_parity == parity for r in found)
        out.append((f"کلاس «{key}» روز/ساعت/زوج‌فرد درست", ok_days and ok_time and ok_parity))
    out.append(("پاسخ فارسی و تمیز", bool(run.replies) and _persian(run.replies[-1]) and _clean(run.replies[-1])))
    return out


def _homework_checks(run: Run) -> list[Check]:
    with session() as s:
        tasks = s.query(Task).all()
        rec = s.query(RecurringEvent).first()
    linked = [t for t in tasks if t.recurring_event_id == rec.id]
    return [
        ("حداقل یک تکلیف ثبت شد", len(tasks) >= 1),
        ("تکلیف به کلاس وصل شد", bool(linked)),
        ("ددلاین = جلسه بعد (سه‌شنبه ۱۸:۰۰)", any(t.due_at == datetime(2026, 10, 20, 18, 0) for t in tasks)),
        ("پاسخ فارسی و تمیز", _persian(run.replies[-1]) and _clean(run.replies[-1])),
    ]


def _availability_checks(run: Run) -> list[Check]:
    reply = run.replies[-1]
    return [
        ("check_availability صدا زده شد", "check_availability" in run.tools),
        ("به تداخل با کلاس اشاره کرد", "کلاس" in reply),
        ("پاسخ فارسی و تمیز", _persian(reply) and _clean(reply)),
    ]


def _planning_checks(run: Run) -> list[Check]:
    with session() as s:
        tasks = s.query(Task).all()
        blocks = s.query(PlannedBlock).count()
    return [
        ("۵ تسک ثبت شد", len(tasks) >= 5),
        ("همه ددلاین دارند", len(tasks) >= 5 and all(t.due_at for t in tasks)),
        ("ددلاین «ارائه» شنبه ۱۰:۰۰", any("ارائه" in t.title and t.due_at == datetime(2026, 10, 24, 10, 0) for t in tasks)),
        ("بلوک کاری برنامه‌ریزی شد", blocks > 0),
        ("پاسخ فارسی و تمیز", _persian(run.replies[-1]) and _clean(run.replies[-1])),
    ]


def _jalali_checks(run: Run) -> list[Check]:
    with session() as s:
        evs = s.query(Event).all()
    return [
        ("رویداد ثبت شد", bool(evs)),
        ("تاریخ ۵ آبان ۱۴۰۵ = ۲۰۲۶-۱۰-۲۷ ساعت ۱۰", any(e.start_at == datetime(2026, 10, 27, 10, 0) for e in evs)),
    ]


def _delete_checks(run: Run) -> list[Check]:
    with session() as s:
        left = s.query(Task).count()
    first = run.metas[0] if run.metas else {}
    return [
        ("در نوبت اول دکمه تأیید نشان داده شد", (first.get("confirm") or {}).get("status") == "pending"),
        ("بعد از «آره» حذف شد", left == 0),
    ]


def _one_task(title_part: str) -> list[Task]:
    with session() as s:
        return [t for t in s.query(Task).all() if title_part in t.title]


def _edit_checks(run: Run) -> list[Check]:
    ts = _one_task("گزارش")
    return [
        ("تسک تکراری ساخته نشد", len(ts) == 1),
        ("ددلاین به شنبه ۱۲:۰۰ تغییر کرد", len(ts) == 1 and ts[0].due_at == datetime(2026, 10, 24, 12, 0)),
        ("پاسخ فارسی و تمیز", _persian(run.replies[-1]) and _clean(run.replies[-1])),
    ]


def _correction_checks(run: Run) -> list[Check]:
    with session() as s:
        evs = s.query(Event).all()
        tasks = s.query(Task).count()
    return [
        ("فقط یک جلسه ثبت شده", len(evs) == 1 and tasks == 0),
        ("ساعت جلسه ۱۸:۰۰ فردا شد", len(evs) == 1 and evs[0].start_at == datetime(2026, 10, 20, 18, 0)),
    ]


def _change_after_add_checks(run: Run) -> list[Check]:
    ts = _one_task("RAG")
    return [
        ("تسک تکراری ساخته نشد", len(ts) == 1),
        ("ددلاین جمعه شد", len(ts) == 1 and ts[0].due_at is not None and ts[0].due_at.date() == datetime(2026, 10, 23).date()),
        ("زمان ۴ ساعت شد", len(ts) == 1 and ts[0].estimated_minutes == 240),
    ]


def _deadline_plan_checks(run: Run) -> list[Check]:
    import re as _re

    ts = _one_task("شبکه")
    with session() as s:
        blocks = (s.query(PlannedBlock).filter(PlannedBlock.task_id == ts[0].id).all() if len(ts) == 1 else [])
    reply = run.replies[-1]
    return [
        ("تسک با ددلاین چهارشنبه ۱۸:۰۰ و ۵ ساعت", len(ts) == 1 and ts[0].due_at == datetime(2026, 10, 21, 18, 0)
         and ts[0].estimated_minutes == 300),
        ("زمان کار قبل از ددلاین چیده شد", sum((b.end_at - b.start_at).total_seconds() for b in blocks) >= 300 * 60),
        ("در جواب گفت کی رویش کار کند", bool(_re.search(r"(امروز|امشب|فردا|سه‌?شنبه|چهارشنبه|دوشنبه)", reply))
         and bool(_re.search(r"[0-9۰-۹]{1,2}[:٫:]?[0-9۰-۹]{0,2}", reply))),
        ("نپرسید «ثبت کنم؟»", not _re.search(r"(ثبت|اضافه|برنامه‌?ریزی) کنم\s*؟", reply)),
    ]


def _bulk_checks(run: Run) -> list[Check]:
    with session() as s:
        dues = [t.due_at for t in s.query(Task).all()]
    first = run.metas[0] if run.metas else {}
    return [
        ("برای تغییر گروهی دکمه تأیید نشان داد", (first.get("confirm") or {}).get("status") == "pending"),
        ("بعد از «آره» همه منتقل شدند", len(dues) == 3 and all(d is not None and d >= datetime(2026, 10, 26) for d in dues)),
    ]


def _act_checks(run: Run) -> list[Check]:
    import re as _re

    with session() as s:
        n = s.query(Task).count() + s.query(Event).count()
    return [
        ("ثبت شد بدون اینکه اجازه بگیرد", n >= 1),
        ("سؤال «ثبت کنم؟» نپرسید", not _re.search(r"(ثبت|اضافه|یادداشت) (کنم|بکنم)\s*؟", run.replies[-1])),
    ]


def _ielts_checks(run: Run) -> list[Check]:
    with session() as s:
        sc = s.query(IeltsScore).filter(IeltsScore.skill == "writing").first()
    return [
        ("grade_ielts_writing صدا زده شد", "grade_ielts_writing" in run.tools),
        ("نمره ذخیره شد و منطقی است (۴ تا ۸)", bool(sc) and 4 <= sc.band <= 8),
        ("بازخورد فارسی", _persian(run.replies[-1])),
    ]


def _habit_checks(run: Run) -> list[Check]:
    with session() as s:
        total = s.query(func.coalesce(func.sum(HabitLog.value), 0)).scalar() or 0
    return [("۴۰ دقیقه ورزش ثبت شد", float(total) == 40.0), ("پاسخ فارسی", _persian(run.replies[-1]))]


def _exam_checks(run: Run) -> list[Check]:
    with session() as s:
        exam = s.query(Event).filter(Event.kind == "exam").first()
        subs = s.query(Task).filter(Task.parent_id.is_not(None)).count()
    return [
        ("plan_exam_prep صدا زده شد", "plan_exam_prep" in run.tools),
        ("امتحان ۱۰ آبان ساعت ۹ ثبت شد", bool(exam) and exam.start_at == datetime(2026, 11, 1, 9, 0)),
        ("جلسه‌های مطالعه ساخته شد", subs >= 3),
    ]


def _chitchat_checks(run: Run) -> list[Check]:
    return [("پاسخ فارسی", _persian(run.replies[-1])), ("بدون نشت متن سیستمی", _clean(run.replies[-1])),
            ("ابزار بی‌دلیل صدا زده نشد", not [t for t in run.tools if t not in ("get_agenda", "list_tasks")])]


# ------------------------------------------- failures from the user's real chats (Oct 20 – Nov 2, 2026)
# Clock: Monday 2026-10-19 10:00 = ۲۷ مهر ۱۴۰۵. ۱ آبان = 2026-10-23, ۹ آذر = 2026-11-30 (Monday).
MIDTERM_DAY = datetime(2026, 11, 30)


def _seed_midterm_wrong() -> None:
    with session() as s:
        s.add(Event(title="میانترم اتوماتا", kind="exam", start_at=MIDTERM_DAY.replace(hour=9),
                    end_at=MIDTERM_DAY.replace(hour=10)))


def _seed_midterm_twice() -> None:
    _seed_midterm_wrong()
    with session() as s:
        s.add(Event(title="میانترم اتوماتا", kind="exam", start_at=MIDTERM_DAY.replace(hour=16, minute=30),
                    end_at=MIDTERM_DAY.replace(hour=18)))


def _seed_minilab() -> None:
    with session() as s:
        s.add(Task(title="مینی‌لب: پیاده‌سازی Dashboard Widget", category="university", estimated_minutes=90,
                   due_at=datetime(2026, 10, 20, 9, 0)))
        s.add(Task(title="تمرین برنامه‌سازی موبایل", category="university", estimated_minutes=120,
                   due_at=datetime(2026, 10, 30, 23, 59)))


def _seed_pending_reminders() -> None:
    """A task with a reminder and a check-in due in the next hours (what kept pinging the user)."""
    with session() as s:
        t = Task(title="تمرین اتوماتا", category="university", estimated_minutes=90, due_at=datetime(2026, 10, 19, 23, 59))
        s.add(t)
        s.flush()
        s.add(Followup(kind="deadline", due_at=datetime(2026, 10, 19, 10, 40), task_id=t.id, dedupe_key="eval:deadline"))
        s.add(Followup(kind="task_checkin", due_at=datetime(2026, 10, 19, 12, 30), task_id=t.id,
                       dedupe_key="eval:checkin"))


def _seed_today_class() -> None:
    with session() as s:
        s.add(RecurringEvent(title="مفاهیم پیشرفته کامپیوتر", kind="university_class", weekdays=["sat", "mon"],
                             start_time="16:30", end_time="18:00", followup=True))
    with session() as s:
        followups.generate(s, FIXED_NOW)


def _seed_car_project() -> None:
    with session() as s:
        s.add(Project(name="tahami supercars", aliases=["پروژه ماشین"], category="work"))


def _due_kinds(at: datetime) -> list[str]:
    with session() as s:
        return [f.kind for f in followups.due_now(s, at)]


def _deadline_list_checks(run: Run) -> list[Check]:
    with session() as s:
        tasks = s.query(Task).all()
        evs = s.query(Event).all()

    def items(key: str) -> list[datetime]:
        return [t.due_at for t in tasks if key in t.title and t.due_at] + [e.start_at for e in evs if key in e.title]

    def due_on(key: str, day: int, month: int = 10) -> bool:
        got = items(key)
        return len(got) == 1 and got[0].date() == datetime(2026, month, day).date() and got[0].hour == 23

    midterm = items("اتوماتا")
    return [
        ("مدار: ۲۹ مهر = چهارشنبه ۲۱ اکتبر ۲۳:۵۹", due_on("مدار", 21)),
        ("مینی‌لب: ۱ آبان = جمعه ۲۳ اکتبر ۲۳:۵۹", due_on("مینی", 23)),
        ("کوییز هوش: ۳ آبان = یکشنبه ۲۵ اکتبر ۲۳:۵۹", due_on("هوش", 25)),
        ("میانترم اتوماتا: ۹ آذر ۱۶:۳۰، فقط یک بار", len(midterm) == 1
         and midterm[0] == MIDTERM_DAY.replace(hour=16, minute=30)),
        ("پاسخ فارسی و تمیز", _persian(run.replies[-1]) and _clean(run.replies[-1])),
    ]


def _midterm_fix_checks(run: Run) -> list[Check]:
    with session() as s:
        evs = s.query(Event).all()
    return [
        ("میانترم دوم ساخته نشد", len(evs) == 1),
        ("ساعت همان میانترم ۱۶:۳۰ شد", len(evs) == 1 and evs[0].start_at == MIDTERM_DAY.replace(hour=16, minute=30)),
    ]


def _delete_wrong_checks(run: Run) -> list[Check]:
    with session() as s:
        evs = s.query(Event).all()
    return [
        ("مورد اشتباه (۹ صبح) پاک شد", not [e for e in evs if e.start_at.hour == 9]),
        ("مورد درست (۱۶:۳۰) ماند", len(evs) == 1 and evs[0].start_at.hour == 16),
        ("بعد از «آره» دوباره تأیید نخواست", (run.metas[-1].get("confirm") or {}).get("status") != "pending"),
    ]


def _done_fuzzy_checks(run: Run) -> list[Check]:
    with session() as s:
        lab = s.query(Task).filter(Task.title.contains("Dashboard")).one()
        other = s.query(Task).filter(Task.title.contains("تمرین")).one()
    return [
        ("مینی‌لب داشبورد انجام‌شده ثبت شد", lab.status == "done"),
        ("تمرین موبایل دست نخورد", other.status != "done"),
        ("پاسخ فارسی و تمیز", _persian(run.replies[-1]) and _clean(run.replies[-1])),
    ]


def _dnd_checks(run: Run) -> list[Check]:
    early = _due_kinds(datetime(2026, 10, 19, 10, 45)) + _due_kinds(datetime(2026, 10, 19, 12, 35))
    return [
        ("تا ۱۳:۳۰ هیچ پیگیری‌ای نرفت", not early),
        ("یادآوری‌ها حذف نشدند، بعد از ۱۳:۳۰ می‌آیند", bool(_due_kinds(datetime(2026, 10, 19, 13, 45)))),
        ("پاسخ فارسی و تمیز", _persian(run.replies[-1]) and _clean(run.replies[-1])),
    ]


def _quiet_now_checks(run: Run) -> list[Check]:
    reply = run.replies[-1]
    early = _due_kinds(datetime(2026, 10, 19, 10, 45))
    return [
        ("حداقل تا یک ساعت پیگیری نرفت", not early),
        ("جواب کوتاه و بدون سؤال", len(reply) <= 160 and "؟" not in reply),
        ("پاسخ فارسی و تمیز", _persian(reply) and _clean(reply)),
    ]


def _skip_class_checks(run: Run) -> list[Check]:
    after_class = _due_kinds(datetime(2026, 10, 19, 18, 30))
    return [
        ("بعد از کلاس نپرسید «تکلیف چی دادن؟»", "post_class" not in after_class),
        ("نپرسید کدوم کلاس (امروز فقط یکی هست)", "کدوم" not in run.replies[-1] and "کدام" not in run.replies[-1]),
        ("پاسخ فارسی و تمیز", _persian(run.replies[-1]) and _clean(run.replies[-1])),
    ]


def _project_task_checks(run: Run) -> list[Check]:
    ts = _one_task("داکیومنت")
    with session() as s:
        pid = s.query(Project.id).scalar()
    return [
        ("تسک ثبت شد", len(ts) == 1),
        ("به «پروژه ماشین» وصل شد", len(ts) == 1 and ts[0].project_id == pid),
        ("ددلاین امشب", len(ts) == 1 and ts[0].due_at is not None and ts[0].due_at.date() == FIXED_NOW.date()
         and ts[0].due_at.hour >= 21),
        ("خطای خام به کاربر نشان داده نشد", _clean(run.replies[-1])),
    ]


def _wellbeing_checks(run: Run) -> list[Check]:
    reply = run.replies[-1]
    return [
        ("حالش را پرسید", "؟" in reply),
        ("سراغ کارها و ددلاین‌ها نرفت", not re.search(r"(ددلاین|تسک|تکلیف|برنامه‌ات)", reply)),
        ("ابزار نوشتنی صدا زده نشد", not [t for t in run.tools if t not in ("get_agenda", "list_tasks", "remember")]),
        ("پاسخ فارسی و تمیز", _persian(reply) and _clean(reply)),
    ]


def _ask_tonight_checks(run: Run) -> list[Check]:
    with session() as s:
        fs = s.query(Followup).filter(Followup.created_by == "agent").all()
    tonight = [f for f in fs if f.due_at.date() == FIXED_NOW.date() and 20 <= f.due_at.hour <= 23]
    return [
        ("یادآوری برای امشب گذاشت", bool(tonight)),
        ("ددلاین ساختگی نساخت", not [t for t in _one_task("") if t.due_at]),
        ("پاسخ فارسی و تمیز", _persian(run.replies[-1]) and _clean(run.replies[-1])),
    ]


SCENARIOS = [
    Scenario("homework", "تکلیف بعد از کلاس", ["تازه از کلاس زبان اومدم، برای جلسه بعد یه رایتینگ تسک ۲ درباره تکنولوژی داد"],
             _homework_checks, _seed_ielts_class),
    Scenario("availability", "می‌رسم برم بیرون؟", ["پنجشنبه ساعت ۵ تا ۸ عصر دوستام گفتن بریم بیرون، می‌تونم برم؟"],
             _availability_checks, _seed_busy_week),
    Scenario("planning", "چند کار با ددلاین + برنامه‌ریزی", [
        "این هفته کلی کار دارم: تمرین سری ۴ پایگاه داده تا چهارشنبه ظهر (۲ ساعت)، ارائه سمینار شنبه ساعت ۱۰ صبح "
        "(۴ ساعت آماده‌سازی)، گزارش کاری تا پنجشنبه ساعت ۵ (۳ ساعت)، خوندن فصل ۶ کتاب هوش مصنوعی تا یکشنبه (۹۰ دقیقه) "
        "و ریدینگ آیلتس تا سه‌شنبه شب (یک ساعت). برام برنامه بریز"], _planning_checks),
    Scenario("jalali", "تاریخ شمسی", ["۵ آبان ساعت ۱۰ صبح دندون‌پزشکی دارم، حدود یک ساعت"], _jalali_checks),
    Scenario("timetable", "خواندن عکس برنامه کلاسی", ["این برنامه کلاسی این ترممه"], _timetable_checks,
             image="timetable.png"),
    Scenario("delete", "تأیید قبل از حذف", ["تسک خرید کتاب رو پاک کن", "آره پاکش کن"], _delete_checks, _seed_book_task),
    Scenario("ielts", "تصحیح رایتینگ آیلتس", ["این رایتینگ تسک ۲ امروزمه، تصحیحش کن:\n\n" + ESSAY], _ielts_checks),
    Scenario("habit", "ثبت عادت", ["امروز ۴۰ دقیقه دویدم"], _habit_checks, _seed_habit),
    Scenario("exam", "برنامه امتحان", ["امتحان ریاضی ۲ ده آبان ساعت ۹ صبحه. حدود ۱۰ ساعت مطالعه لازم دارم، "
                                       "فصل‌های مشتق، انتگرال و سری‌ها. برام برنامه بچین"], _exam_checks),
    Scenario("chitchat", "گپ ساده", ["سلام! خوبی؟"], _chitchat_checks),
    Scenario("edit", "ویرایش به‌جای تسک تکراری", ["گزارش پروژه ماشین رو بنداز برای شنبه ساعت ۱۲"], _edit_checks,
             _seed_recent_task),
    Scenario("correction", "اصلاح ساعت جلسه", ["فردا ساعت ۵ عصر با مدیرم جلسه دارم", "ببخشید ساعتش ۶ هست نه ۵"],
             _correction_checks),
    Scenario("change", "تغییر بعد از ثبت", ["تا پنجشنبه باید مقاله RAG رو بخونم، حدود ۳ ساعت",
                                            "نه ددلاینش جمعه‌ست، و حدود ۴ ساعت طول می‌کشه"], _change_after_add_checks),
    Scenario("deadline", "برنامه تا ددلاین", ["تا چهارشنبه ساعت ۶ عصر باید پروژه درس شبکه رو تحویل بدم، حدود ۵ ساعت کار داره"],
             _deadline_plan_checks),
    Scenario("bulk", "تغییر گروهی با دکمه تأیید", ["این هفته خیلی شلوغم، همه کارهای این هفته رو بنداز برای هفته بعد همون روز و ساعت",
                                                  "آره"], _bulk_checks, _seed_week_tasks),
    Scenario("act", "ثبت بدون اجازه گرفتن", ["فردا باید برم بانک کارت جدید بگیرم"], _act_checks),
    # from the real chats
    Scenario("deadline_list", "لیست ددلاین با تاریخ شمسی", [
        "۲۹ مهر ددلاین تمرین مدارالک\n۱ آبان ددلاین مینی‌لب موبایل\n۳ آبان رسوندن سوال های کوییز هوش\n"
        "میانترم اتوماتا: ۹ آذر ۴:۳۰ تا ۶ عصر\n\nاینایی که ددلاینن ساعت ۱۱:۵۹ شب اون تاریخه مهلتشون"],
        _deadline_list_checks),
    Scenario("midterm_fix", "اصلاح ساعت به‌جای رویداد دوم", ["میانترم اتوماتا سر کلاس خودش برگزار میشه، ۴:۳۰ تا ۶ عصر"],
             _midterm_fix_checks, _seed_midterm_wrong),
    Scenario("delete_wrong", "پاک کردن مورد تکراری اشتباه", ["چرا ۹ آذر ۲ تا میانترم اتوماتا ثبت کردی؟ اشتباهه رو پاک کن",
                                                           "اره حذفش کن"], _delete_wrong_checks, _seed_midterm_twice),
    Scenario("done_fuzzy", "«تموم شد» با اسم متفاوت", ["داشبورد موبایل تموم شد کامل"], _done_fuzzy_checks, _seed_minilab),
    Scenario("dnd", "مزاحم نشو تا ساعت مشخص", ["الان تا ۱:۳۰ میخوام ناهار بخورم و استراحت کنم\nمزاحمم نشو"], _dnd_checks,
             _seed_pending_reminders),
    Scenario("quiet_now", "ساکت شو، دارم کار می‌کنم", ["دهنتو ببند دارم اتوماتا مینویسم"], _quiet_now_checks,
             _seed_pending_reminders),
    Scenario("skip_class", "امروز نمیرم کلاس", ["امروز نمیرم کلاس"], _skip_class_checks, _seed_today_class),
    Scenario("project_task", "تسک برای پروژه با اسم مستعار", [
        "تا آخر امشب برای قسمت گیم پروژه ماشین باید یه داکیومنت دربیارم و به کارفرما تحویل بدم"],
        _project_task_checks, _seed_car_project),
    Scenario("wellbeing", "احوال‌پرسی، نه پیگیری کار", ["از صبح کم حالمو پرسیدی"], _wellbeing_checks, _seed_busy_week),
    Scenario("ask_tonight", "شب ازم بپرس", ["یه ددلاین داده که گفت روی cw هست ولی نرفتم چک کنم، شب ازم بپرس بهت بگم"],
             _ask_tonight_checks),
]


# ---------------------------------------------------------------- runner
async def run_scenario(sc: Scenario, model: str) -> dict[str, Any]:
    from .agent import core

    from . import memory

    tmp = tempfile.mkdtemp(prefix="peygir-eval-")
    db.init_engine(f"sqlite:///{tmp}/eval.db")
    memory.reset_cache()
    prefs.set("user_name", "علی")
    sc.seed()
    run = Run()
    started = time.monotonic()
    error = None
    try:
        for i, turn in enumerate(sc.turns):
            atts = []
            if sc.image and i == 0:
                atts.append(core.Attachment("image", (EVAL_DIR / sc.image).read_bytes(), "image/png", sc.image))
            _, reply = await core.handle_user_message(turn, "web", atts, model=model)
            run.replies.append(reply["content"])
            run.metas.append(reply.get("meta") or {})
            run.tools += [a["tool"] for a in reply["meta"].get("actions", [])]
            with session() as s:
                # include tools hidden from chips (read-only ones)
                for m in s.query(Message).filter(Message.id == reply["id"]):
                    run.tools += [x.split("(")[0].split(" ")[0] for x in (m.meta or {}).get("action_log", [])]
        checks = sc.checks(run)
    except Exception as e:  # noqa: BLE001
        error = str(e)
        checks = [("اجرا بدون خطا", False)]
    elapsed = time.monotonic() - started
    with session() as s:
        # synthetic scenario data only — safe to show in the (public) report
        state = [f"task «{t.title}» due {timeutil.iso(t.due_at) if t.due_at else '-'} est {t.estimated_minutes}"
                 for t in s.query(Task).limit(8)]
        state += [f"event «{e.title}» {timeutil.iso(e.start_at)}" for e in s.query(Event).limit(4)]
        cost = s.query(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).scalar() or 0.0
        toks = s.query(func.coalesce(func.sum(LlmUsage.prompt_tokens + LlmUsage.completion_tokens), 0)).scalar() or 0
        served = sorted({u.model for u in s.query(LlmUsage).filter(LlmUsage.ok.is_(True)).all()})
    return {
        "scenario": sc.name,
        "title": sc.title,
        "model": model,
        "served_by": served,
        "checks": [{"name": n, "ok": ok} for n, ok in checks],
        "passed": sum(1 for _, ok in checks if ok),
        "total": len(checks),
        "seconds": round(elapsed, 1),
        "cost_usd": round(cost, 5),
        "tokens": int(toks),
        "replies": run.replies,
        "tools": run.tools,
        "state": state,
        "error": error,
    }


def render(results: list[dict[str, Any]]) -> str:
    models = list(dict.fromkeys(r["model"] for r in results))
    lines = ["# مقایسه مدل‌ها روی سناریوهای پیگیر", "",
             f"تاریخ اجرا: {datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
             "| مدل | امتیاز | درصد | زمان کل (ث) | هزینه ($) | توکن |", "|---|---|---|---|---|---|"]
    for m in models:
        rs = [r for r in results if r["model"] == m]
        p, t = sum(r["passed"] for r in rs), sum(r["total"] for r in rs)
        lines.append(f"| `{m}` | {p}/{t} | {round(100 * p / t) if t else 0}٪ | {sum(r['seconds'] for r in rs):.0f} | "
                     f"{sum(r['cost_usd'] for r in rs):.4f} | {sum(r['tokens'] for r in rs):,} |")
    lines += ["", "## جزئیات سناریوها", ""]
    header = "| سناریو | " + " | ".join(f"`{m}`" for m in models) + " |"
    lines += [header, "|---|" + "---|" * len(models)]
    for sc in dict.fromkeys(r["scenario"] for r in results):
        row = [next(r["title"] for r in results if r["scenario"] == sc)]
        for m in models:
            r = next((x for x in results if x["scenario"] == sc and x["model"] == m), None)
            row.append("—" if r is None else f"{r['passed']}/{r['total']}" + (" ⚠️" if r["error"] else ""))
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "## چک‌های ناموفق", ""]
    for r in results:
        bad = [c["name"] for c in r["checks"] if not c["ok"]]
        if bad or r["error"]:
            lines.append(f"- `{r['model']}` / {r['title']}: " + "، ".join(bad) + (f" — خطا: {r['error']}" if r["error"] else ""))
            lines.append(f"  - tools: {', '.join(dict.fromkeys(r['tools']))}")
            lines += [f"  - {x}" for x in r.get("state", [])]
    return "\n".join(lines) + "\n"


async def main_async(models: list[str], names: list[str] | None, out: Path, repeat: int) -> list[dict[str, Any]]:
    st = get_settings()
    st.background_jobs = False
    st.scheduler_enabled = False
    timeutil.now = lambda: FIXED_NOW  # type: ignore[assignment]
    scenarios = [s for s in SCENARIOS if not names or s.name in names]
    results = []
    for model in models:
        for sc in scenarios:
            for _ in range(repeat):
                r = await run_scenario(sc, model)
                results.append(r)
                print(f"[{model}] {sc.title}: {r['passed']}/{r['total']} ({r['seconds']}s, ${r['cost_usd']})", flush=True)
    out.write_text(render(results), encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare GapGPT models on Peygir scenarios")
    ap.add_argument("--models", required=True, help="comma-separated model ids")
    ap.add_argument("--scenarios", default="", help=f"subset: {','.join(s.name for s in SCENARIOS)}")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--out", default="evals-report.md")
    args = ap.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    names = [n.strip() for n in args.scenarios.split(",") if n.strip()] or None
    asyncio.run(main_async(models, names, Path(args.out), args.repeat))
    print(f"\nreport: {args.out}")


if __name__ == "__main__":
    main()
