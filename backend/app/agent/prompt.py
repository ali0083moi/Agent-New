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
- You only see some of your tools. If a request needs one you don't have (classes/timetable, planning, habits,
  IELTS, projects/professors, reports, settings, reminders, goals), call `load_tools` first, then continue.
- Never invent ids. Use only ids that appear in the context below or in tool results. Refer to projects and
  professors by NAME (`project="پروژه ماشین"`, `contact="Dr. X"`) — the server resolves names and nicknames.
- Dates: pass 'YYYY-MM-DD HH:MM' (use the calendar below). "امشب/تا آخر امشب" = today 23:59; "قبل از <روز>" =
  the day before at 23:59; a Gregorian month name (نوامبر، دسامبر…) is a Gregorian date, a Persian one (آبان…) Jalali.
- If a tool returns an error or warnings, fix the arguments and call it again in the same turn; don't give up and
  don't blame "a technical problem" unless it fails twice.

## Your job
- Know the user's goals, classes, work, projects, tasks and deadlines, and keep them on track.
- Capture EVERYTHING: whenever the user mentions something to do, a homework, a deadline, an exam, a meeting, an
  appointment or a plan, save it right away (add_task / add_event / classes via load_tools). Never just say "ok"
  without saving. If the deadline or the duration is unclear, make a sensible estimate, save, and say what you
  assumed (one short line) — ask a question only when it really matters.
- Corrections are edits: when the user changes or corrects something they mentioned before («نه ساعتش ۶ بود»،
  «ددلاینش رو بکن پنجشنبه»، «تغییرش بده»، «اون یکی رو …»، «عنوانش رو عوض کن») → update_task / update_event on the
  EXISTING item (id from "Recently added/changed" or the [actions done] notes, or pass its title as task=/event=).
  Never add a second copy. If add_task/add_event says a similar item exists, update that one instead.
- Deadlines: add_task / update_task return `plan` — the work sessions the planner found before the deadline (with
  where the user will be). Tell the user in one or two lines when they'll work on it, like a person would («امشب
  ۲۰ تا ۲۱:۳۰ خونه، پنجشنبه بین دو کلاس توی دانشگاه»), and say clearly if it doesn't fit and what you suggest.
- Think about the user's real day (see "The user's day"): they can't work while in class, at the company or on
  the road; between two classes at the university they can do reading/light work; a metro ride suits vocab or
  listening; after a long day at work, evenings are for lighter tasks unless a deadline is close.
- Estimate durations realistically from what you know about the user (see "Learned about the user"); include
  travel and getting-ready time for things that happen somewhere else (see "Places & travel").
- Homework after classes: create a task per assignment with due_next_session_of=<class id> (deadline = start of
  the next session). Reminders are then automatic.
- You are first of all a reliable reminder that knows their day. «X دقیقه/ساعت دیگه یادم بنداز …» →
  schedule_followup(in_minutes=X, note=<the thing, short>) — never compute the clock time yourself; «ساعت ۵ …» → at.
  Then confirm with the exact time the tool returns («⏰ ساعت ۱۷:۱۵ یادت می‌ندازم زنگ بزنی به رضا»).
- Follow up: the system wakes you to send reminders (after classes, before deadlines, leaving, morning brief; in
  the coach style also work sessions and check-ins). When the user answers, act on it: log work, update progress,
  mark tasks done, re-plan if they fell behind.
- «مزاحمم نشو تا …», «بذار بخوابم», «دارم کار می‌کنم», «خفه شو», «دهنتو ببند»: call do_not_disturb (their time;
  «تا صبح» = wake time; while they work on something ≈ 2 hours) and answer in ONE short line with no question —
  no apology essay, no suggestions. If they say what they're working on, also start_focus.
- «امروز نمیرم کلاس», «کلاس نرفتم», «جلسه بعد کنسله» → skip_class (the class today/that day); don't ask which one
  when only one fits. «کلاس برگزار نمی‌شه» for good → update the class.
- Past is past: an overdue task was asked about once — don't bring it up again unless the user does. A quiz, exam,
  interview or presentation whose time has passed is over: never plan time to prepare for it, never remind about it;
  if it's still open, close it (update_task status=cancelled) and move on. If the user says they finished something
  («تموم شد», «دادم»، «انجامش دادم») → complete_task on the matching open task, even if they name it differently.
- Know where they are (see "The user's day"): if they say they're at university / at work / on the way, believe it
  and don't push work they can't do there.
- Time tracking: starts working → start_focus; finished/stopped → stop_focus; reports time spent → log_work.
- "Can I go out / can I do X at time T?": ALWAYS call check_availability first (it weighs conflicts AND the workload
  before upcoming deadlines). Verdict (آره / آره ولی فشرده می‌شه / نه) + reason in 1-3 lines; if they go, add_event.
- Forwarded messages and attached files (class group, professor, manager, syllabus): extract every homework, exam,
  deadline or meeting and save it, linked to the matching class/project when obvious. Then confirm briefly.
- Cost: you run on a cheap model. Before writing something where quality really matters (an email to a professor,
  SOP/CV/cover letter, a recommendation request) or building a complex plan, call use_strong_model once.
- Act, don't ask: adding, editing, completing, moving one thing → just do it and say what you did (the user can
  press «برگردون»). NEVER end with a yes/no question like «ثبتش کنم؟» / «می‌خوای تغییرش بدم؟» — either do it, or
  if it is a deletion, a bulk/big change or two readings are really plausible, call `ask_approval` with the concrete
  tool calls: the user gets ✅/❌ buttons and ✅ runs them immediately. Delete tools show these buttons by
  themselves. A pending proposal (listed in the context) is never redone by you. "برگردون"/undo → undo_last.
- Be honest about productivity: praise real progress, call out procrastination kindly but clearly.
- Confirm briefly what you saved (e.g. "✅ ثبت شد: ... — ددلاین سه‌شنبه ۱۸:۰۰"). Don't show IDs or raw JSON. Light
  Markdown (bold, bullets); no tables or headings in normal chat.
- Bracketed notes in the history such as "[actions done: ...]" or "[proactive ... message you sent]" are system
  annotations for you; never write them in your replies.

## Dates
All times are local ({timezone}). Tools accept 'YYYY-MM-DD HH:MM' in Gregorian (preferred) or Jalali (year < 1700).
Use the calendar below to convert Persian weekday names / Jalali dates. Week starts on Saturday (شنبه).
"شب" for a class usually means 18:00-22:00. A weekday without a date means the nearest upcoming one.
"""


def _fmt_minutes(m: int | None) -> str:
    if not m:
        return "?"
    h, mm = divmod(int(m), 60)
    return f"{h}h{mm:02d}m" if h else f"{mm}m"


MEMORY_ORDER = {"profile": 0, "work": 1, "study": 2, "routine": 3, "person": 4, "preference": 5}


def _calendar(today, days: int) -> str:
    out = []
    for i in range(days):
        d = today + timedelta(days=i)
        j = timeutil.jalali(d)
        tag = " today" if i == 0 else (" tomorrow" if i == 1 else "")
        out.append(f"{timeutil.WEEKDAY_FA[timeutil.weekday_key(d)]} {timeutil.weekday_key(d)} "
                   f"{j.year}/{j.month:02d}/{j.day:02d} = {d.isoformat()}{tag}")
    return "\n".join(out)


def _task_line(s: Session, t: Task) -> str:
    rem = planner.remaining_minutes(s, t)
    return (
        ("  ↳ " if t.parent_id else "- ")
        + f"[task {t.id}] {t.title} | {t.category}"
        + (" | doing" if t.status == "doing" else "")
        + (f" | p{t.priority}" if t.priority != 2 else "")
        + (f" | due {timeutil.iso(t.due_at)}" if t.due_at else "")
        + (" | OVERDUE (asked once; don't plan or bring it up unless the user does)"
           if t.due_at and t.due_at < timeutil.now() else "")
        + f" | est {_fmt_minutes(t.estimated_minutes)}"
        + (f" left {_fmt_minutes(rem)}" if rem != t.estimated_minutes else "")
        + (f" | {t.progress}%" if t.progress else "")
        + (f" | energy {t.energy}" if t.energy != "normal" else "")
        + (f" | project {t.project_id}" if t.project_id else "")
        + (f" | class {t.recurring_event_id}" if t.recurring_event_id else "")
    )


def _recent_lines(s: Session, now) -> list[str]:
    """Items created/changed in the last two days — what «اون», «همون»، «تغییرش بده» usually refer to."""
    from ..db import Event, Journal

    models = {"tasks": ("task", Task), "events": ("event", Event), "recurring_events": ("class", RecurringEvent)}
    rows = (s.query(Journal).filter(Journal.created_at >= now - timedelta(hours=48), Journal.undone.is_(False),
                                    Journal.table.in_(list(models)), Journal.op.in_(["create", "update"]))
            .order_by(Journal.id.desc()).limit(60).all())
    out, seen = [], set()
    for j in rows:
        if (j.table, j.row_id) in seen:
            continue
        seen.add((j.table, j.row_id))
        tag, model = models[j.table]
        obj = s.get(model, j.row_id)
        if obj is None:
            continue
        when = ""
        if tag == "task":
            when = f" | {obj.status}" + (f" | due {timeutil.iso(obj.due_at)}" if obj.due_at else "")
        elif tag == "event":
            when = f" | {timeutil.iso(obj.start_at)}"
        out.append(f"- [{tag} {obj.id}] {obj.title}{when} ({'added' if j.op == 'create' else 'changed'} {timeutil.iso(j.created_at)[5:]})")
        if len(out) >= 8:
            break
    return ["\n## Recently added/changed (newest first — «اون/همون/تغییرش بده» usually means one of these)"] + out if out else []


def build_context(s: Session, groups: list[str] | set[str] = (), mode: str = "chat") -> str:
    """Live snapshot of the user's world. mode="compose" is a slimmer version for proactive messages."""
    groups = set(groups)
    slim = mode == "compose"
    p = prefs.all_prefs(s)
    now = timeutil.now()
    today = now.date()
    j = timeutil.jalali(today)
    lines: list[str] = ["## Now"]
    lines.append(
        f"{timeutil.WEEKDAY_FA[timeutil.weekday_key(today)]} ({timeutil.weekday_key(today)}) "
        f"{today.isoformat()} — جلالی {j.year}/{j.month:02d}/{j.day:02d} ({timeutil.JALALI_MONTHS[j.month - 1]}) — "
        f"time {now.strftime('%H:%M')}"
    )
    if not slim:
        lines.append("\n## Calendar (weekday, Jalali = Gregorian)")
        lines.append(_calendar(today, 14))

    lines.append("\n## User preferences")
    lines.append(
        f"name: {p.get('user_name') or '(unknown — ask casually at some point)'}; wake {p['wake_time']}, sleep {p['sleep_time']}; "
        f"productive window {p['work_start']}-{p['work_end']}; daily focus goal {_fmt_minutes(p['daily_focus_goal_minutes'])}; "
        f"follow-up intensity {p['followup_intensity']}; employer: {p.get('employer_name') or '-'}"
        + (f"; semester_start {p['semester_start']}" if p.get("semester_start") else "")
    )
    if p.get("semester_start"):
        wn = cal.week_number(today, p["semester_start"])
        lines.append(f"current university week number: {wn} ({'فرد/odd' if wn % 2 else 'زوج/even'})")

    from .. import places

    lines += places.context_lines(s)
    from .. import followups as followups_mod

    mute = followups_mod.quiet_until(s, now)
    if mute is not None:
        lines.append(f"\n## Do not disturb until {timeutil.iso(mute)} (asked by the user; no follow-ups until then)")
    lines.append(f"Follow-up style: {p.get('followup_style') or 'reminder'}")
    from .. import pending

    lines += pending.context_lines(s)

    mems = sorted(s.query(Memory).all(), key=lambda m: (MEMORY_ORDER.get(m.category, 9), -m.id))
    cap = 15 if slim else 40
    lines.append("\n## What you know about the user (memories)")
    lines += [f"- [m{m.id}] ({m.category}) {m.content}" for m in mems[:cap]] or ["- (nothing yet)"]
    if len(mems) > cap:
        lines.append(f"- (+{len(mems) - cap} more; relevant ones are retrieved below when they matter)")

    from ..db import Project
    from .. import projects as projects_mod

    projs = s.query(Project).filter(Project.status != "done").all()
    if projs or not slim:
        lines.append("\n## Projects (refer to them by name or nickname)")
        for pr in projs:
            d = projects_mod.project_dict(s, pr)
            lines.append(
                f"- [project {pr.id}] {pr.name}" + (f" ({', '.join(pr.aliases)})" if pr.aliases else "")
                + (f" | client {pr.client}" if pr.client else "")
                + f" | open tasks {d['open_tasks']}, this week {d['minutes_this_week']} min"
            )
        if not projs:
            lines.append("- (none yet)")
    pipe = projects_mod.pipeline(s)
    if pipe["contacts"]:
        if "apply" in groups:
            lines.append("\n## Supervisor search (contacts)")
            for c in pipe["contacts"][:25]:
                lines.append(
                    f"- [contact {c['id']}] {c['name']} ({c['organization']}) — {c['status']}"
                    + (f", last contact {c['last_contact']}" if c["last_contact"] else "")
                    + (f", follow-up {c['next_followup']}" if c["next_followup"] else "")
                )
        else:
            lines.append(f"\nSupervisor search: {len(pipe['contacts'])} professors tracked (load_tools apply for details).")
    from .. import applications as apps_mod

    lines += apps_mod.context_lines(s, full="apply" in groups)

    goals = s.query(Goal).filter(Goal.status == "active").all()
    if goals:
        lines.append("\n## Active goals")
        lines += [
            f"- [g{g.id}] {g.title}" + (f" — target {g.target_date.isoformat()}" if g.target_date else "")
            + (f" — {g.progress_note}" if g.progress_note else "")
            for g in goals
        ]

    if not slim:
        recs = s.query(RecurringEvent).filter(RecurringEvent.active.is_(True)).all()
        lines.append("\n## Weekly classes / recurring commitments")
        for r in recs:
            nxt = cal.next_occurrence(s, r, now)
            lines.append(
                f"- [class {r.id}] {r.title} ({r.kind}) {','.join(r.weekdays)} {r.start_time}-{r.end_time}"
                + (f" parity:{r.week_parity}" if r.week_parity != "all" else "")
                + (f" @{r.location}" if r.location else "")
                + (f" next {timeutil.iso(nxt.start)}" if nxt else "")
            )
        if not recs:
            lines.append("- (none yet — ask about their class schedule / timetable photo when relevant)")

    limit = 12 if slim else 30
    q = s.query(Task).filter(Task.status.in_(["todo", "doing"]))
    total = q.count()
    tasks = q.order_by(Task.due_at.is_(None), Task.due_at, Task.priority).limit(limit).all()
    lines.append("\n## Open tasks")
    lines += [_task_line(s, t) for t in tasks] or ["- (none)"]
    if total > limit:
        lines.append(f"- (+{total - limit} more without near deadlines — list_tasks to see them)")

    lines += _recent_lines(s, now)
    from .. import daymap

    try:
        lines += daymap.context_lines(s, now)
    except Exception:  # noqa: BLE001 — the day map is a nice-to-have; never break the prompt
        import logging

        logging.getLogger(__name__).exception("day map failed")

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
        if "habits" in groups:
            lines.append("\n## Habits")
            lines += [
                f"- [habit {h['id']}] {h['title']}: target {h['target']} {h['unit']}/{h['period']}, today {h['today']}, "
                f"streak {h['streak']} (best {h['best_streak']})" + (f", reminder {h['reminder_time']}" if h["reminder_time"] else "")
                for h in hb
            ]
        else:
            lines.append("\nHabits: " + "; ".join(f"{h['title']} (today {h['today']}/{h['target']}, streak {h['streak']})" for h in hb))
    commits = habits_mod.auto_evaluate(s, today)
    if commits:
        lines.append("\n## Today's commitments")
        lines += [
            f"- [commitment {c['id']}] {c['text']} — {c['status']}"
            + (f" ({c['logged_minutes']}/{c['target_minutes']} min)" if c["target_minutes"] else "")
            for c in commits
        ]
    if not slim:
        factors = insights.estimation_factors(s)
        peaks = insights.peak_hours(s)
        lines.append("\n## Learned about the user")
        lines.append(f"- peak productive hours: {insights.describe_hours(peaks['hours'])}" + ("" if peaks["learned"] else " (default, not enough data yet)"))
        lines += [f"- {x}" for x in insights.estimation_lines(factors) + insights.reminder_lines(insights.response_stats(s))]
        lines += [f"- {x}" for x in insights.duration_lines(s)]
        lines += [f"- {x}" for x in insights.study_lines(insights.study_profile(s))]
        if p.get("study_hours"):
            lines.append(f"- likes to study / do deep work at {p['study_hours']} (the planner uses this)")
        else:
            lines.append("- when they like to study is unknown — when it comes up, ask and save it (update_preferences study_hours)")
    sp = prefs.get("speaking_practice", s=s)
    if "ielts" in groups or sp:
        ip = ielts_mod.progress(s)
        lines.append("\n## IELTS")
        lines.append(
            f"- target band {ip['target_band']}" + (f", exam {ip['exam_date']} ({ip['days_left']} days left)" if ip["exam_date"] else "")
            + f"; latest: {ip['latest'] or 'no scores yet'}; vocab {ip['vocab']['total']} words, {ip['vocab']['due_today']} due today"
        )
        if sp:
            lines.append(f"- speaking practice active until {sp.get('until')}: «{sp.get('question')}» (part {sp.get('part')})")

    if not slim:
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


def persona(s: Session) -> str:
    """The static part of the system prompt (identical on every call, so providers can cache it)."""
    from ..config import get_settings

    p = prefs.all_prefs(s)
    return PERSONA.format(assistant_name=p.get("assistant_name") or "پیگیر", timezone=get_settings().timezone)


def system_prompt(s: Session, groups: list[str] | set[str] = (), mode: str = "chat") -> str:
    from . import toolsets

    guide = toolsets.guides(groups)
    return persona(s) + ("\n## Instructions for the loaded tools\n" + guide + "\n" if guide else "") + "\n" + build_context(s, groups, mode)
