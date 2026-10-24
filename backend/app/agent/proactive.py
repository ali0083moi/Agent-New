"""Turn due follow-ups into messages and send them."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from .. import cal, followups, gamify, habits, ielts, notify, planner, prefs, reports, rescue, stats, timeutil
from ..bus import bus
from ..db import Followup, Habit, PlannedBlock, RecurringEvent, Task, session
from . import core

log = logging.getLogger(__name__)


def _task_line(s, t: Task) -> str:
    parts = [f"«{t.title}»"]
    if t.due_at:
        parts.append(f"ددلاین {timeutil.fa_datetime(t.due_at)}")
    parts.append(f"باقی‌مانده تقریبی {timeutil.fa_duration(planner.remaining_minutes(s, t))}")
    if t.progress:
        parts.append(f"پیشرفت {timeutil.fa_digits(t.progress)}٪")
    return " — ".join(parts)


def build(f: Followup) -> tuple[str, str, list[str]] | None:
    """Return (instruction for the LLM, fallback text, quick replies) for a follow-up."""
    with session() as s:
        p = prefs.all_prefs(s)
        now = timeutil.now()
        t = s.get(Task, f.task_id) if f.task_id else None
        payload = f.payload or {}

        if f.kind == "post_class":
            r = s.get(RecurringEvent, f.recurring_event_id)
            if r is None:
                return None
            nxt = cal.next_occurrence(s, r, now)
            linked = s.query(Task).filter(Task.recurring_event_id == r.id, Task.status.in_(["todo", "doing"])).all()
            nxt_txt = timeutil.fa_datetime(nxt.start) if nxt else "نامشخص"
            instr = (
                f"Class just ended: «{r.title}» ({cal.KIND_FA.get(r.kind, r.kind)}, class id {r.id}), "
                f"{r.start_time}-{r.end_time}. Next session: {nxt_txt}.\n"
                f"Open tasks already linked to this class: {[_task_line(s, x) for x in linked] or 'none'}.\n"
                "Ask briefly how the class was and exactly what homework/tasks were given for next session "
                "(so you can save them and follow up). If there are still-open linked tasks from before, ask about them too."
            )
            fallback = f"کلاس «{r.title}» تموم شد 👀 چطور بود؟ برای جلسه بعد ({nxt_txt}) تکلیف یا کاری دادن؟"
            return instr, fallback, ["تکلیفی نداشتیم", "کلاس نرفتم"]

        if f.kind in ("deadline", "task_checkin", "overdue") and t is not None:
            blocks = (
                s.query(PlannedBlock)
                .filter(PlannedBlock.task_id == t.id, PlannedBlock.start_at > now, PlannedBlock.status == "planned")
                .order_by(PlannedBlock.start_at)
                .limit(3)
                .all()
            )
            blocks_txt = ", ".join(f"{timeutil.fa_datetime(b.start_at)}" for b in blocks) or "none"
            if f.kind == "deadline":
                hrs = payload.get("hours_before")
                instr = (
                    f"Deadline reminder (~{hrs}h left) for task {t.id}: {_task_line(s, t)}. Status: {t.status}. "
                    f"Upcoming planned blocks for it: {blocks_txt}. Remind them and ask where they are with it."
                )
                fallback = f"⏰ یادت نره: {_task_line(s, t)}. در چه حالیه؟"
            elif f.kind == "task_checkin":
                instr = (
                    f"Mid-way progress check for task {t.id}: {_task_line(s, t)}. Planned blocks: {blocks_txt}. "
                    "Ask how far along they are (percent) and whether the plan still works."
                )
                fallback = f"یه سر بزنیم به {_task_line(s, t)} — چقدرش پیش رفته؟"
            else:
                instr = (
                    f"The deadline of task {t.id} passed and it's not marked done: {_task_line(s, t)}. "
                    "Ask what happened: done already (then you'll mark it), or needs a new deadline?"
                )
                fallback = f"ددلاین «{t.title}» گذشت. انجامش دادی یا باید یه ددلاین جدید بذاریم؟"
            return instr, fallback, ["انجامش دادم ✅", "هنوز نه", "ددلاین رو جابه‌جا کن"]

        if f.kind in ("block_start", "block_end"):
            b = s.get(PlannedBlock, f.block_id) if f.block_id else None
            if b is None or t is None:
                return None
            span = f"{timeutil.fa_time(b.start_at)} تا {timeutil.fa_time(b.end_at)}"
            if f.kind == "block_start":
                instr = (
                    f"A planned focus block (block id {b.id}) for task {t.id} {_task_line(s, t)} starts now ({span}). "
                    "Nudge them to start; if they say they're starting, you will start the focus timer."
                )
                fallback = f"▶️ وقت کار روی «{t.title}» ({span})ه. شروع کنیم؟"
                return instr, fallback, ["شروع کردم ▶️", "نیم ساعت دیگه", "امروز نمی‌رسم"]
            instr = (
                f"The planned focus block (block id {b.id}) for task {t.id} «{t.title}» ({span}) just ended. "
                "Ask how it went and how much they got done, so you can log it (log_work with block_id) and update progress."
            )
            fallback = f"بلوک «{t.title}» ({span}) تموم شد. چقدر پیش رفتی؟"
            return instr, fallback, ["کامل انجام شد ✅", "نصفش رو", "انجام ندادم"]

        if f.kind == "daily_brief":
            plan_txt = ""
            if p.get("auto_plan_daily", True) and planner.open_tasks(s):
                res = planner.plan(s, days=7)
                today_blocks = [b for b in res.blocks if b["start"][:10] == now.date().isoformat()]
                plan_txt = f"\nToday's planned work blocks: {today_blocks}\nPlanner warnings: {res.warnings}"
                bus.publish("data_changed", ["schedule", "followups"])
            day = cal.agenda(s, now.date(), 1)[0]
            soon = (
                s.query(Task)
                .filter(Task.status.in_(["todo", "doing"]), Task.due_at.is_not(None), Task.due_at <= now + timedelta(days=3))
                .order_by(Task.due_at)
                .all()
            )
            y = stats.productivity(s, now.date() - timedelta(days=1), now.date() - timedelta(days=1))
            hb = [h for h in habits.all_habits(s) if h["period"] == "daily"]
            g = gamify.summary(s)
            instr = (
                "Morning brief. Write a friendly, energizing but compact plan for today: classes/events, the work blocks, "
                "and the most important 1-3 things to finish. Mention deadlines in the next 3 days. Then ask what they "
                "commit to today (one concrete promise, e.g. '۲ ساعت رایتینگ') so you can record it with add_commitment. "
                "One short motivating line.\n"
                f"Today's agenda: {day['items']}\nDue within 3 days: {[_task_line(s, x) for x in soon]}"
                f"{plan_txt}\nYesterday productive minutes: {y['total_productive_minutes']} (goal {y['daily_goal_minutes']})"
                f"\nDaily habits & streaks: {[(h['title'], h['streak']) for h in hb]}"
                f"\nLevel {g['level']} ({g['title']}), this week's score so far {g['weekly_score']['score']}/100"
            )
            lines = [f"☀️ صبح بخیر! برنامه امروز ({timeutil.fa_date(now)}):"]
            lines += [f"- {timeutil.fa_digits(it['start'][11:])} {it['title']}" for it in day["items"]] or ["- برنامه ثابتی نداری"]
            if soon:
                lines.append("ددلاین‌های نزدیک:")
                lines += [f"- {_task_line(s, x)}" for x in soon]
            lines.append("امروز چه قولی به خودت می‌دی؟ 🤝")
            return instr, "\n".join(lines), ["۲ ساعت کار عمیق", "تموم کردن یه تسک"]

        if f.kind == "daily_review":
            st = stats.productivity(s, now.date(), now.date())
            tomorrow = cal.agenda(s, now.date() + timedelta(days=1), 1)[0]
            commits = habits.auto_evaluate(s, now.date())
            hb = [h for h in habits.all_habits(s) if h["period"] == "daily"]
            pts = gamify.points_between(s, now.date(), now.date())
            instr = (
                "Evening review. Summarize today honestly in a few lines: productive time vs goal, tasks completed, "
                "planned blocks done vs skipped, today's commitments (kept or not — ask about the pending ones and record "
                "the outcome with update_commitment), habits not done yet, points earned today. Then ask what else they "
                "did today that isn't logged yet (to log it) and mention tomorrow's key items briefly.\n"
                f"Today stats: productive {st['total_productive_minutes']} min (goal {st['daily_goal_minutes']}), "
                f"by category {st['by_category']}, completed {[x['title'] for x in st['completed_tasks']]}, "
                f"blocks {st['blocks']}.\nCommitments: {commits}\n"
                f"Habits: {[(h['title'], h['today'], h['target'], h['streak']) for h in hb]}\nPoints today: {pts['total']}\n"
                f"Tomorrow: {tomorrow['items']}"
            )
            fallback = (
                f"🌙 جمع‌بندی امروز: {timeutil.fa_duration(st['total_productive_minutes'])} کار مفید ثبت شده "
                f"(هدف: {timeutil.fa_duration(st['daily_goal_minutes'])}). کار دیگه‌ای هم انجام دادی که ثبت نشده؟"
            )
            return instr, fallback, ["همین بود", "یه کار دیگه هم کردم"]

        if f.kind == "idle_checkin":
            now_items = [it.to_dict() for it in cal.items_between(s, now - timedelta(hours=1), now + timedelta(hours=3))]
            top = sorted(planner.open_tasks(s), key=planner._task_key)[:5]
            instr = (
                f"Idle check-in: no contact for {p.get('idle_checkin_hours')}+ hours during their productive window. "
                f"Planned around now: {now_items}. Top open tasks: {[_task_line(s, x) for x in top]}.\n"
                "Ask casually what they're doing right now / if they're working on something, and whether there's "
                "any new task you should know about. Keep it to 2 lines."
            )
            fallback = "👋 چه خبر؟ الان مشغول چه کاری هستی؟ کار جدیدی هم هست که باید ثبت کنم؟"
            return instr, fallback, ["دارم کار می‌کنم", "استراحتم", "کار جدید دارم"]

        if f.kind == "focus_long":
            cur = stats.active_focus(s)
            if cur is None:
                return None
            d = stats.focus_to_dict(s, cur)
            instr = f"The focus timer for «{d['task'] or d['description']}» has been running {d['elapsed_minutes']} min. Ask if they're still working or forgot to stop it."
            fallback = f"⏱ تایمر «{d['task'] or d['description']}» {timeutil.fa_duration(d['elapsed_minutes'])}ه روشنه. هنوز داری کار می‌کنی؟"
            return instr, fallback, ["هنوز دارم کار می‌کنم", "یادم رفت خاموشش کنم"]

        if f.kind == "rescue":
            data = rescue.analyze(s)
            if not data.get("needed"):
                return None
            instr = (
                "RESCUE MODE: with the current workload the user will miss deadlines. Calmly explain which tasks are at "
                "risk and by how much (short), then offer 2-3 concrete ways out: postpone specific lower-priority tasks "
                "(and how much time that frees), add extra time per day, ask for an extension (offer to draft the "
                "message to the professor/manager in Persian), or shrink the scope. Ask which option they pick; when they "
                "decide, apply it with tools (update_task due/priority/status, update_preferences, plan_schedule).\n"
                f"Analysis: {data}"
            )
            names = "، ".join(f"«{t['title']}»" for t in data["at_risk"])
            fallback = (f"🛟 با این حجم کار به {names} نمی‌رسی (حدود {timeutil.fa_duration(data['shortfall_minutes'])} کم داریم). "
                        "بیا با هم تصمیم بگیریم چی رو عقب بندازیم یا تمدید بگیریم.")
            return instr, fallback, ["چی رو عقب بندازیم؟", "متن درخواست تمدید رو بنویس", "روزی یه ساعت بیشتر کار می‌کنم"]

        if f.kind == "weekly_planning":
            ws_next = timeutil.week_start(now.date()) + timedelta(days=7)
            nxt = cal.agenda(s, ws_next, 7)
            due_next = (
                s.query(Task).filter(Task.status.in_(["todo", "doing"]), Task.due_at.is_not(None),
                                     Task.due_at < timeutil.at(ws_next + timedelta(days=7), "00:00")).order_by(Task.due_at).all()
            )
            week = stats.productivity(s, timeutil.week_start(now.date()), now.date())
            score = gamify.weekly_score(s, timeutil.week_start(now.date()))
            instr = (
                "Weekly planning session for NEXT week. Briefly recap this week (productive time, weekly score), list next "
                "week's fixed commitments and known deadlines, then ask: any new tasks, exams, events or goals for next week? "
                "Tell them that after they answer you will lay out the whole week (then you'll call plan_schedule with days=9). "
                "Keep it structured but short.\n"
                f"This week: {week['total_productive_minutes']} productive min, score {score}\n"
                f"Next week agenda: {[(d['weekday_fa'], [(i['type'], i['title'], i['start'][11:]) for i in d['items'] if i['type'] != 'block']) for d in nxt]}\n"
                f"Known deadlines: {[_task_line(s, t) for t in due_next]}"
            )
            fallback = "🗓 وقت برنامه‌ریزی هفته بعده! کار، امتحان یا قرار جدیدی برای هفته بعد داری؟ بگو تا کل هفته رو بچینم."
            return instr, fallback, ["بریم بچینیم", "چیز جدیدی ندارم، فقط بچین"]

        if f.kind == "habit_reminder":
            h = s.get(Habit, (f.payload or {}).get("habit_id"))
            if h is None:
                return None
            st = habits.habit_stats(s, h)
            instr = (f"Habit nudge: «{h.title}» isn't done yet today (target {h.target} {st['unit_fa']}, current streak "
                     f"{st['streak']} days, best {st['best_streak']}). Short, encouraging; mention the streak at stake. "
                     f"If they say they did it, log it with log_habit (habit id {h.id}).")
            fallback = f"🔁 «{h.title}» امروز هنوز انجام نشده" + (f" — زنجیره {timeutil.fa_digits(st['streak'])} روزه‌ات رو نشکن! 🔥" if st["streak"] else "")
            return instr, fallback, ["انجامش دادم ✅", "امروز نمی‌شه"]

        if f.kind == "vocab_review":
            words = ielts.due_words(s, int(prefs.get("vocab_daily_count", s=s) or 8))
            if not words:
                return None
            listing = [{"id": w.id, "word": w.word, "box": w.box} for w in words]
            instr = ("Daily IELTS vocabulary review (spaced repetition). Ask the user the meaning of these words (show only "
                     "the words, numbered; do NOT reveal meanings). When they answer, check each one and call vocab_review "
                     f"for every word, then show the correct meanings for the ones they missed.\nWords: {listing}")
            fallback = "📇 مرور لغت امروز — معنی این‌ها چیه؟\n" + "\n".join(f"{timeutil.fa_digits(i + 1)}. {w.word}" for i, w in enumerate(words))
            return instr, fallback, ["بلد نیستم، معنی‌ها رو بگو"]

        if f.kind == "contact_followup":
            from ..db import Contact
            from .. import projects as projects_mod

            c = s.get(Contact, f.contact_id) if f.contact_id else None
            if c is None:
                return None
            d = projects_mod.contact_dict(c)
            if c.status == "to_contact":
                instr = (f"Supervisor search: it's time to email {c.name} ({c.organization}, {c.field}). Remind the user and "
                         "offer to draft a tailored cold email in English (short, specific to their research). "
                         f"Contact: {json.dumps(d, ensure_ascii=False, default=str)}")
                fallback = f"🎓 قرار بود به {c.name} ({c.organization}) ایمیل بزنی. متن ایمیل رو برات بنویسم؟"
                return instr, fallback, ["متن ایمیل رو بنویس", "فرستادم ✅", "بعداً"]
            instr = (f"Supervisor search follow-up: {d['days_since_contact']} days since the user emailed {c.name} "
                     f"({c.organization}) and no reply is recorded. Ask if they got a reply; if not, offer to draft a "
                     "short polite follow-up email in English (reference the first email, add one new detail). "
                     f"When the user answers, update the contact with update_contact. Contact: {json.dumps(d, ensure_ascii=False, default=str)}")
            fallback = (f"🎓 {timeutil.fa_digits(d['days_since_contact'] or 0)} روز از ایمیلت به {c.name} گذشته. "
                        "جواب داده؟ اگه نه، یه ایمیل پیگیری کوتاه برات بنویسم؟")
            return instr, fallback, ["جواب داده", "متن پیگیری رو بنویس", "بیخیالش"]

        if f.kind == "application_deadline":
            from .. import applications
            from ..db import Application

            a = s.get(Application, f.application_id) if f.application_id else None
            if a is None:
                return None
            d = applications.app_dict(s, a)
            days = (f.payload or {}).get("days")
            instr = (f"University application deadline in {days} day(s): {applications.a_label(a)} ({a.country}), deadline "
                     f"{d['deadline']}. Checklist {d['done']}/{d['total']}; still missing: {'، '.join(d['missing']) or 'nothing'}. "
                     "Remind the user, say what to do today to make it (be concrete: which item first), and offer help "
                     "(e.g. review the SOP, draft a reminder to a recommender). If everything is done, tell them to submit.")
            missing = "، ".join(d["missing"][:4]) or "همه چیز آماده است"
            fallback = (f"🎓 {timeutil.fa_digits(days)} روز تا ددلاین اپلای **{applications.a_label(a)}** مانده. "
                        f"باقی‌مانده: {missing}")
            return instr, fallback, ["کمکم کن", "ارسالش کردم ✅"]

        if f.kind == "contact_reply":
            from ..db import Contact

            c = s.get(Contact, f.contact_id) if f.contact_id else None
            if c is None:
                return None
            pl = f.payload or {}
            instr = (f"Supervisor search: {c.name} ({c.organization}) just replied to the user's email (subject: "
                     f"«{pl.get('subject', '')}», {pl.get('date', '')}). Their email:\n---\n{pl.get('text', '')[:2000]}\n---\n"
                     "Tell the user in Persian, in 2-4 lines, what the professor said (positive? asks for CV/a call/"
                     "interview? no position?) and the best next step. Offer to draft the reply in English. "
                     "(The contact status is already 'replied'.)")
            fallback = f"📩 {c.name} جواب ایمیلت رو داد: «{pl.get('subject', '')}». بخونمش و جوابشو برات بنویسم؟"
            return instr, fallback, ["جوابشو بنویس", "بعداً می‌خونم"]

        if f.kind == "leave":
            pl = f.payload or {}
            start = timeutil.parse_datetime(pl["start"])
            leave = timeutil.parse_datetime(pl["leave_at"])
            hm = lambda d: timeutil.fa_digits(d.strftime("%H:%M"))  # noqa: E731
            trip = f"مسیر حدود {timeutil.fa_digits(pl['travel'])} دقیقه" + (" با ترافیک معمول این ساعت" if pl.get("traffic") else "")
            if pl.get("from_home"):
                text = (f"🚪 وقت حاضر شدنه: **{pl['title']}** ساعت {hm(start)} در «{pl['place']}».\n"
                        f"ساعت {hm(leave)} باید از خونه راه بیفتی ({trip}).")
            else:
                text = (f"🚗 ساعت {hm(leave)} از «{pl['origin']}» راه بیفت تا به **{pl['title']}** "
                        f"(ساعت {hm(start)} در «{pl['place']}») برسی — {trip}.")
            return "", text, ["راه افتادم", "امروز نمی‌رم"]

        if f.kind == "custom":
            extra = f" Related task: {_task_line(s, t)}." if t else ""
            instr = f"Reminder the user (or you) scheduled: «{f.note}».{extra} Deliver it naturally."
            return instr, f"⏰ یادآوری: {f.note}", []
    return None


async def dispatch(fid: int) -> dict[str, Any] | None:
    with session() as s:
        f = s.get(Followup, fid)
        if f is None or f.status != "pending":
            return None
        s.expunge(f)
    if f.kind == "weekly_report":
        async with core.lock:
            rep = await reports.generate(0)
            await notify.deliver("📄 **گزارش هفتگی آماده شد.** اولی برای کارفرماست (قابل فوروارد)، دومی مرور شخصی خودت.",
                                 "weekly_report", meta={"report_id": rep.id})
            await notify.deliver(rep.work_report, "weekly_report", meta={"report_id": rep.id, "part": "work"})
            msg = await notify.deliver(rep.personal_report, "weekly_report", meta={"report_id": rep.id, "part": "personal"})
        _mark_sent(fid)
        bus.publish("data_changed", ["reports"])
        return msg
    built = build(f)
    if built is None:
        _mark(fid, "cancelled")
        return None
    instr, fallback, quick = built
    async with core.lock:
        # an empty instruction means the fixed text is the message (no model call needed)
        text = await core.compose(instr, fallback, strong=f.kind in STRONG_KINDS) if instr else fallback
        msg = await notify.deliver(text, f.kind, meta={"followup_id": fid, "quick_replies": quick})
    _mark_sent(fid)
    return msg


def _mark(fid: int, status: str) -> None:
    with session() as s:
        f = s.get(Followup, fid)
        if f:
            f.status = status
            if status == "sent":
                f.sent_at = timeutil.now()


def _mark_sent(fid: int) -> None:
    _mark(fid, "sent")
    bus.publish("data_changed", ["followups"])


# proactive messages worth the stronger model (the weekly planning session lays out the whole next week)
STRONG_KINDS = {"weekly_planning"}

# task reminders that fall due together are sent as one message (one model call instead of several)
BATCHABLE = {"deadline", "task_checkin", "overdue"}


async def dispatch_batch(fids: list[int]) -> dict[str, Any] | None:
    parts: list[tuple[int, str, str]] = []
    for fid in fids:
        with session() as s:
            f = s.get(Followup, fid)
            if f is None or f.status != "pending":
                continue
            s.expunge(f)
        built = build(f)
        if built is None:
            _mark(fid, "cancelled")
            continue
        parts.append((fid, built[0], built[1]))
    if len(parts) == 1:
        return await dispatch(parts[0][0])
    if not parts:
        return None
    instr = ("Several task reminders are due at the same time. Combine them into ONE short message: a compact "
             "bullet list, most urgent first, then one line of advice on what to do now.\n"
             + "\n".join(f"- {p[1]}" for p in parts))
    fallback = "\n".join(p[2] for p in parts)
    async with core.lock:
        text = await core.compose(instr, fallback)
        msg = await notify.deliver(text, "deadline", meta={"followup_ids": [p[0] for p in parts]})
    for p in parts:
        _mark(p[0], "sent")
    bus.publish("data_changed", ["followups"])
    return msg


async def tick() -> int:
    """One scheduler iteration: generate + send due follow-ups."""
    with session() as s:
        followups.generate(s)
        due = [(f.id, f.kind) for f in followups.due_now(s)]
    batch = [fid for fid, kind in due if kind in BATCHABLE]
    singles = [fid for fid, kind in due if kind not in BATCHABLE or len(batch) < 2]
    sent = 0
    if len(batch) >= 2:
        try:
            if await dispatch_batch(batch):
                sent += 1
        except Exception:  # noqa: BLE001
            log.exception("dispatch of follow-ups %s failed", batch)
    for fid in singles:
        try:
            if await dispatch(fid):
                sent += 1
        except Exception:  # noqa: BLE001
            log.exception("dispatch of follow-up %s failed", fid)
    from .. import outbox

    try:
        await outbox.flush()
    except Exception:  # noqa: BLE001
        log.exception("outbox flush failed")
    return sent
