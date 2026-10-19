"""Turn due follow-ups into messages and send them."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from .. import cal, followups, notify, planner, prefs, reports, stats, timeutil
from ..bus import bus
from ..db import Followup, PlannedBlock, RecurringEvent, Task, session
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
            instr = (
                "Morning brief. Write a friendly, energizing but compact plan for today: classes/events, the work blocks, "
                "and the most important 1-3 things to finish. Mention deadlines in the next 3 days. One short motivating line.\n"
                f"Today's agenda: {day['items']}\nDue within 3 days: {[_task_line(s, x) for x in soon]}"
                f"{plan_txt}\nYesterday productive minutes: {y['total_productive_minutes']} (goal {y['daily_goal_minutes']})"
            )
            lines = [f"☀️ صبح بخیر! برنامه امروز ({timeutil.fa_date(now)}):"]
            lines += [f"- {timeutil.fa_digits(it['start'][11:])} {it['title']}" for it in day["items"]] or ["- برنامه ثابتی نداری"]
            if soon:
                lines.append("ددلاین‌های نزدیک:")
                lines += [f"- {_task_line(s, x)}" for x in soon]
            return instr, "\n".join(lines), []

        if f.kind == "daily_review":
            st = stats.productivity(s, now.date(), now.date())
            tomorrow = cal.agenda(s, now.date() + timedelta(days=1), 1)[0]
            instr = (
                "Evening review. Summarize today honestly in a few lines: productive time vs goal, tasks completed, "
                "planned blocks done vs skipped. Then ask what else they did today that isn't logged yet (to log it) "
                "and mention tomorrow's key items briefly.\n"
                f"Today stats: productive {st['total_productive_minutes']} min (goal {st['daily_goal_minutes']}), "
                f"by category {st['by_category']}, completed {[x['title'] for x in st['completed_tasks']]}, "
                f"blocks {st['blocks']}.\nTomorrow: {tomorrow['items']}"
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
        text = await core.compose(instr, fallback)
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


async def tick() -> int:
    """One scheduler iteration: generate + send due follow-ups."""
    with session() as s:
        followups.generate(s)
        ids = [f.id for f in followups.due_now(s)]
    sent = 0
    for fid in ids:
        try:
            if await dispatch(fid):
                sent += 1
        except Exception:  # noqa: BLE001
            log.exception("dispatch of follow-up %s failed", fid)
    return sent
