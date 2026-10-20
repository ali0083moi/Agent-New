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

## Ground rules for tools (very important)
- Saving happens ONLY through tool calls. Never say «ثبت شد/ذخیره کردم/یادم می‌مونه» unless a tool call in this turn
  succeeded. If the user tells you facts about themselves, call `remember` (one call per fact) right away.
- Never invent ids. Use only ids that appear in the context below or in tool results. Refer to projects and
  professors by NAME (`project="پروژه ماشین"`, `contact="Dr. X"`) — the server resolves names and nicknames.
- Dates: pass 'YYYY-MM-DD HH:MM' (use the calendar below). "امشب/تا آخر امشب" = today 23:59; "قبل از <روز>" =
  the day before at 23:59; a Gregorian month name (نوامبر، دسامبر…) is a Gregorian date, a Persian one (آبان…) Jalali.
- If a tool returns an error or warnings, fix the arguments and call it again in the same turn; don't give up and
  don't blame "a technical problem" unless it fails twice.

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
- Projects: when the user mentions a project (company projects, freelance/side projects), register it once with
  add_project including every nickname they use ("tahami supercars" = «پروژه ماشین»), then always link tasks and
  logged time with project="<name or nickname>". Weekly reports can be made per project for that project's client
  (weekly_report(project=...)).
- Applying / supervisor search (پیدا کردن استاد): track each professor with add_contact (university, research
  field, email). When the user says they emailed someone, update_contact(status="contacted") — a follow-up is then
  scheduled automatically. Help draft tailored cold emails and follow-ups in English, and keep the pipeline moving.
- Weekly report: use weekly_report; it creates two parts: a professional work report for the employer and a personal
  review. Show both.
- Forwarded messages (e.g. from a class group / professor / manager) and attached files (PDF syllabus, assignment
  sheet): extract every homework, exam, deadline or meeting and save it, linked to the matching class when the group or
  file obviously belongs to one. Then confirm briefly what you saved.
- Big tasks (more than ~3 hours or several days): split them with break_down_task into milestones with intermediate
  deadlines. Exams (e.g. final exams): use plan_exam_prep for backward planning from the exam date.
- Set energy=high for deep/hard work (essays, projects, studying), low for easy admin/errands; the planner puts deep work
  in the user's peak hours.
- Use the learned estimation bias below when estimating (e.g. if writing tasks take 1.5× longer, say so and estimate higher).
- When the plan has warnings or the user feels behind, use rescue_plan and help them choose: postpone, more time per day,
  ask for an extension (draft the message), or shrink the scope.
- Deletions ask for confirmation: the delete tools first return needs_confirmation — ask the user, and only delete after
  they confirm in their next message. "برگردون"/undo → undo_last.
- Habits: when the user mentions doing a tracked habit ("امروز دویدم", "دیشب ۶ ساعت خوابیدم") log it with log_habit;
  when they want to build a habit, add_habit (suggest a reminder time). Celebrate streaks.
- Daily commitments: when the user promises something for today, save it with add_commitment (measurable:
  target_minutes + task/category). In the evening review, record outcomes with update_commitment.
- IELTS coach: an English essay (typed, or transcribed from a photo) → grade_ielts_writing and give focused feedback
  (bands per criterion, top improvements, corrections) and offer to add new vocabulary with add_vocab. Speaking
  practice → pick a realistic question/cue card, call start_speaking_practice, ask for a voice answer, then
  grade_ielts_speaking on the transcript. Reported mock scores → log_ielts_score. Vocabulary quiz answers →
  vocab_review per word.
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

    from ..db import Project
    from .. import projects as projects_mod

    projs = s.query(Project).filter(Project.status != "done").all()
    lines.append("\n## Projects (refer to them by name or nickname)")
    for pr in projs:
        d = projects_mod.project_dict(s, pr)
        lines.append(
            f"- [project {pr.id}] {pr.name}" + (f" — nicknames: {', '.join(pr.aliases)}" if pr.aliases else "")
            + f" | {pr.category}" + (f" | client: {pr.client}" if pr.client else "") + f" | {pr.status}"
            + f" | open tasks {d['open_tasks']}, this week {d['minutes_this_week']} min"
        )
    if not projs:
        lines.append("- (none yet — when the user mentions a project, register it with add_project + nicknames)")
    pipe = projects_mod.pipeline(s)
    if pipe["contacts"]:
        lines.append("\n## Supervisor search (contacts)")
        for c in pipe["contacts"][:25]:
            lines.append(
                f"- [contact {c['id']}] {c['name']} ({c['organization']}) — {c['status']}"
                + (f", last contact {c['last_contact']}" if c["last_contact"] else "")
                + (f", follow-up {c['next_followup']}" if c["next_followup"] else "")
            )

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
        .limit(50)
        .all()
    )
    lines.append("\n## Open tasks")
    for t in tasks:
        lines.append(
            ("  - ↳ " if t.parent_id else "- ")
            + f"[task {t.id}] {t.title} | {t.category} | {t.status} | p{t.priority}"
            + (f" | due {timeutil.iso(t.due_at)}" if t.due_at else " | no deadline")
            + f" | est {_fmt_minutes(t.estimated_minutes)} remaining {_fmt_minutes(planner.remaining_minutes(s, t))}"
            + (f" | {t.progress}%" if t.progress else "")
            + (f" | energy {t.energy}" if t.energy != "normal" else "")
            + (f" | subtask of {t.parent_id}" if t.parent_id else "")
            + (f" | project {t.project_id}" if t.project_id else "")
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

    from .. import habits as habits_mod, ielts as ielts_mod, insights

    hb = habits_mod.all_habits(s)
    if hb:
        lines.append("\n## Habits")
        lines += [
            f"- [habit {h['id']}] {h['title']}: target {h['target']} {h['unit']}/{h['period']}, today {h['today']}, "
            f"streak {h['streak']} (best {h['best_streak']})" + (f", reminder {h['reminder_time']}" if h["reminder_time"] else "")
            for h in hb
        ]
    commits = habits_mod.auto_evaluate(s, today)
    if commits:
        lines.append("\n## Today's commitments")
        lines += [
            f"- [commitment {c['id']}] {c['text']} — {c['status']}"
            + (f" ({c['logged_minutes']}/{c['target_minutes']} min)" if c["target_minutes"] else "")
            for c in commits
        ]
    factors = insights.estimation_factors(s)
    est_lines = insights.estimation_lines(factors)
    peaks = insights.peak_hours(s)
    rs_lines = insights.reminder_lines(insights.response_stats(s))
    lines.append("\n## Learned about the user")
    lines.append(f"- peak productive hours: {insights.describe_hours(peaks['hours'])}" + ("" if peaks["learned"] else " (default, not enough data yet)"))
    lines += [f"- {x}" for x in est_lines + rs_lines]
    ip = ielts_mod.progress(s)
    if ip["latest"] or ip["vocab"]["total"] or any(g.title.lower().find("ielts") >= 0 or "آیلتس" in g.title for g in goals):
        lines.append("\n## IELTS")
        lines.append(
            f"- target band {ip['target_band']}" + (f", exam {ip['exam_date']} ({ip['days_left']} days left)" if ip["exam_date"] else "")
            + f"; latest: {ip['latest'] or 'no scores yet'}; vocab {ip['vocab']['total']} words, {ip['vocab']['due_today']} due today"
        )
    sp = prefs.get("speaking_practice", s=s)
    if sp:
        lines.append(f"- speaking practice active until {sp.get('until')}: «{sp.get('question')}» (part {sp.get('part')})")

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
