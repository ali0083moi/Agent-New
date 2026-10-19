"""System prompt + live context snapshot for the assistant."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from .. import cal, planner, prefs, stats, timeutil
from ..db import Followup, Goal, Memory, RecurringEvent, Task

PERSONA = """You are "{assistant_name}", the user's personal secretary and accountability partner (منشی شخصی و پیگیر).
You talk with the user in Persian (Farsi), casually and warmly — like a smart, caring assistant who knows their life —
short and to the point (this is a chat on Telegram / a web app). Use Persian digits for numbers in your replies.
The user may write in Finglish or English; always answer in Persian unless asked otherwise.

## Your job
- Know the user's goals (e.g. IELTS), classes (university + language), work, tasks and deadlines, and keep them on track.
- Capture EVERYTHING: whenever the user mentions something to do, a homework, a deadline, an exam, a meeting, an
  appointment or a plan, save it right away with tools (add_task / add_event / add_recurring_events). Never just say
  "ok" without saving. If the deadline or the duration is unclear, make a sensible estimate, save, and tell the user
  what you assumed (one short line) — ask a question only when it really matters.
- Homework after classes: when the user tells you the homework of a class, create a task per assignment with
  due_next_session_of=<class id> (deadline = start of the next session). Reminders are then automatic.
- Follow up: you will be woken up by the system to send check-ins (after classes, before deadlines, at the start/end of
  planned work blocks, morning brief, evening review). When the user answers those, act on it: log work (log_work /
  stop_focus), update progress, mark tasks done, re-plan if they fell behind.
- Time tracking: when the user says they start working → start_focus; finished/stopped → stop_focus. When they report
  time spent ("دو ساعت درس خوندم") → log_work. Unproductive time → log_work with productive=false only if they say so.
- Planning: when asked (or when there are several tasks with deadlines) use plan_schedule and present the plan grouped by
  day, concise. Warn clearly when something won't fit before its deadline, and suggest what to drop/move.
- "Can I go out / can I do X at time T?": ALWAYS call check_availability first. It considers hard conflicts AND the total
  workload vs. free time before upcoming deadlines (they may be free at that hour but still too overloaded to afford it).
  Give a clear verdict (آره / آره ولی فشرده می‌شه / نه) with the reason in 1-3 lines, and if it is a yes-but, propose how
  to compensate (e.g. move a block). If they decide to go, add it with add_event and re-plan if needed.
- Timetable images: an image description/extraction may be attached to the user's message. If it is a class timetable,
  add all classes with add_recurring_events in one call (kind=university_class unless obvious otherwise; follow the
  odd/even-week (فرد/زوج) info if present), then show a compact summary and ask the user to correct anything wrong.
  If odd/even weeks are used and you don't know which week this is, ask "این هفته فرده یا زوج؟" and save it with
  update_preferences(current_week_parity).
- Remember long-term facts about the user with `remember` (job, employer, people, routines, preferences, study goals).
- Weekly report: use weekly_report; it creates two parts: a professional work report for the employer and a personal
  review. Show both.
- Be honest about productivity: praise real progress, call out procrastination kindly but clearly.
- When you do things, confirm briefly what you saved (e.g. "✅ ثبت شد: ... — ددلاین سه‌شنبه ۱۸:۰۰"). Don't list IDs to
  the user. Don't dump raw JSON. Use light Markdown (bold, bullet lists); avoid tables and headings in normal chat.
- Bracketed notes in the history such as "[actions done: ...]" or "[proactive ... message you sent]" are system
  annotations for you; never write them in your replies.
- Never claim you saved/changed something unless the tool call succeeded in this turn.

## Dates
All times are local ({timezone}). Tools accept 'YYYY-MM-DD HH:MM' in Gregorian (preferred) or Jalali (year < 1700).
Use the calendar below to convert Persian weekday names / Jalali dates to Gregorian. Week starts on Saturday (شنبه).
"شب" for a class usually means 18:00-22:00. If the user says a weekday without a date, use the nearest upcoming one.
"""


def _fmt_minutes(m: int | None) -> str:
    if not m:
        return "?"
    h, mm = divmod(int(m), 60)
    return f"{h}h{mm:02d}m" if h else f"{mm}m"


def build_context(s: Session) -> str:
    p = prefs.all_prefs(s)
    now = timeutil.now()
    today = now.date()
    j = timeutil.jalali(today)
    lines: list[str] = []
    lines.append("## Now")
    lines.append(
        f"{timeutil.WEEKDAY_FA[timeutil.weekday_key(today)]} ({timeutil.weekday_key(today)}) "
        f"{today.isoformat()} — جلالی {j.year}/{j.month:02d}/{j.day:02d} ({timeutil.JALALI_MONTHS[j.month - 1]}) — "
        f"time {now.strftime('%H:%M')}"
    )
    lines.append("\n## Calendar (next 14 days)")
    lines.append(timeutil.calendar_table(today, 14))

    lines.append("\n## User preferences")
    lines.append(
        f"name: {p.get('user_name') or '(unknown — ask casually at some point)'}; wake {p['wake_time']}, sleep {p['sleep_time']}; "
        f"productive window {p['work_start']}-{p['work_end']}; daily focus goal {_fmt_minutes(p['daily_focus_goal_minutes'])}; "
        f"follow-up intensity {p['followup_intensity']}; employer: {p.get('employer_name') or '-'}; "
        f"semester_start (odd/even weeks): {p.get('semester_start') or 'unknown'}"
    )
    if p.get("semester_start"):
        wn = cal.week_number(today, p["semester_start"])
        lines.append(f"current university week number: {wn} ({'فرد/odd' if wn % 2 else 'زوج/even'})")

    mems = s.query(Memory).order_by(Memory.id).all()
    lines.append("\n## What you know about the user (memories)")
    lines += [f"- [m{m.id}] ({m.category}) {m.content}" for m in mems] or ["- (nothing yet)"]

    goals = s.query(Goal).filter(Goal.status == "active").all()
    lines.append("\n## Active goals")
    lines += [
        f"- [g{g.id}] {g.title}" + (f" — target {g.target_date.isoformat()}" if g.target_date else "")
        + (f" — {g.progress_note}" if g.progress_note else "")
        for g in goals
    ] or ["- (none)"]

    recs = s.query(RecurringEvent).filter(RecurringEvent.active.is_(True)).all()
    lines.append("\n## Weekly classes / recurring commitments")
    for r in recs:
        nxt = cal.next_occurrence(s, r, now)
        lines.append(
            f"- [class {r.id}] {r.title} ({r.kind}) {','.join(r.weekdays)} {r.start_time}-{r.end_time}"
            + (f" parity:{r.week_parity}" if r.week_parity != "all" else "")
            + (f" @{r.location}" if r.location else "")
            + (" [homework follow-up]" if r.followup else "")
            + (f" next: {timeutil.iso(nxt.start)}" if nxt else "")
        )
    if not recs:
        lines.append("- (none yet — ask the user about their class schedule / timetable photo when relevant)")

    tasks = (
        s.query(Task)
        .filter(Task.status.in_(["todo", "doing"]))
        .order_by(Task.due_at.is_(None), Task.due_at, Task.priority)
        .limit(40)
        .all()
    )
    lines.append("\n## Open tasks")
    for t in tasks:
        lines.append(
            f"- [task {t.id}] {t.title} | {t.category} | {t.status} | p{t.priority}"
            + (f" | due {timeutil.iso(t.due_at)}" if t.due_at else " | no deadline")
            + f" | est {_fmt_minutes(t.estimated_minutes)} remaining {_fmt_minutes(planner.remaining_minutes(s, t))}"
            + (f" | {t.progress}%" if t.progress else "")
            + (f" | class {t.recurring_event_id}" if t.recurring_event_id else "")
        )
    if not tasks:
        lines.append("- (none)")

    lines.append("\n## Today & tomorrow")
    for day in cal.agenda(s, today, 2):
        items = [
            f"{it['start'][11:]}-{it['end'][11:]} {it['type']}: {it['title']}"
            + (f" (block {it['ref_id']}, {it['meta'].get('status')})" if it["type"] == "block" else "")
            for it in day["items"]
        ]
        lines.append(f"- {day['weekday_fa']} {day['date']}: " + ("; ".join(items) if items else "free"))

    cur = stats.active_focus(s)
    if cur is not None:
        f = stats.focus_to_dict(s, cur)
        lines.append(f"\n## Focus timer RUNNING: {f['task'] or f['description']} since {f['start']} ({f['elapsed_minutes']} min)")
    today_min = planner.logged_minutes(s, today)
    lines.append(f"\nProductive time logged today: {_fmt_minutes(today_min) if today_min else '0m'}")

    fus = (
        s.query(Followup)
        .filter(Followup.status == "pending", Followup.due_at <= now + timedelta(days=2))
        .order_by(Followup.due_at)
        .limit(8)
        .all()
    )
    if fus:
        lines.append("\n## Upcoming automatic check-ins")
        lines += [f"- [f{f.id}] {timeutil.iso(f.due_at)} {f.kind} {f.note or ''}".rstrip() for f in fus]
    return "\n".join(lines)


def system_prompt(s: Session) -> str:
    p = prefs.all_prefs(s)
    from ..config import get_settings

    persona = PERSONA.format(assistant_name=p.get("assistant_name") or "پیگیر", timezone=get_settings().timezone)
    return persona + "\n" + build_context(s)
