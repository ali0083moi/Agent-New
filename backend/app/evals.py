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
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func

from . import db, prefs, timeutil
from .config import get_settings
from .db import (Event, Habit, HabitLog, IeltsScore, LlmUsage, Message, PlannedBlock, RecurringEvent, Task,
                 session)

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


# ---------------------------------------------------------------- helpers
def _persian(text: str) -> bool:
    fa = sum(1 for ch in text if "؀" <= ch <= "ۿ")
    return fa >= max(5, len(text) * 0.3)


def _clean(text: str) -> bool:
    return "[actions done" not in text and "[proactive" not in text and "SYSTEM TRIGGER" not in text


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
    return [
        ("در نوبت اول تأیید خواست", "delete_task" in run.tools and "؟" in run.replies[0]),
        ("بعد از «آره» حذف شد", left == 0),
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
