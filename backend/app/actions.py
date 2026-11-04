"""User actions on a message that don't need the LLM: snooze a reminder, undo a turn."""

from __future__ import annotations

from datetime import timedelta

from . import followups, journal, notify, planner, prefs, timeutil
from .bus import bus
from .db import Followup, Message, Task, session

# one-tap answers under a reminder; they run at once, without the model
REMINDER_ACTIONS = {
    "done": "✅ انجام شد",
    "tomorrow": "📅 بذار برای فردا",
    "drop": "🗑 دیگه لازم نیست",
    "snooze10": "⏰ ۱۰ دقیقه دیگه",
    "snooze60": "⏰ یک ساعت دیگه",
}


def actions_for(kind: str, has_task: bool) -> list[dict[str, str]]:
    ids = {"custom": ["done", "snooze10", "snooze60"],
           "overdue": ["done", "tomorrow", "drop"],
           "deadline": ["done", "snooze60"]}.get(kind, [])
    if not has_task and kind != "custom":
        return []
    return [{"id": i, "label": REMINDER_ACTIONS[i]} for i in ids]


def reminder_action(message_id: int, action: str) -> str:
    """Run a reminder button: done (completes the linked task), tomorrow (new deadline), drop, snooze."""
    from .agent import tools

    if action not in REMINDER_ACTIONS:
        return "این دکمه رو نمی‌شناسم"
    with session() as s:
        m = s.get(Message, message_id)
        meta = (m.meta or {}) if m else {}
        if meta.get("reminder_done"):
            return meta["reminder_done"]
        fid = meta.get("followup_id")
        f = s.get(Followup, int(fid)) if fid else None
        if f is None:
            return "این پیام یادآوری نیست"
        now = timeutil.now()
        f.responded_at = f.responded_at or now
        t = s.get(Task, f.task_id) if f.task_id else None
        ctx = tools.Ctx(channel="web", trusted=True, turn_id=f"reminder-{message_id}")
        if action.startswith("snooze"):
            nf = followups.snooze(s, f.id, 10 if action == "snooze10" else 60, now)
            text = f"⏰ باشه، {timeutil.fa_time(nf.due_at)} دوباره یادت می‌ندازم."
        elif t is None:
            text = "👌 باشه" if action == "done" else "این یادآوری به کاری وصل نیست"
        elif action == "done":
            tools.REGISTRY["complete_task"].fn(s, ctx=ctx, task_id=t.id)
            text = f"✅ «{t.title}» انجام‌شده ثبت شد."
        elif action == "drop":
            journal.before_update(s, ctx, t, f"لغو «{t.title}»")
            t.status = "cancelled"
            text = f"🗑 «{t.title}» از لیست برداشته شد."
        else:  # tomorrow: same clock time (or end of day), from today
            base = t.due_at or timeutil.at(now.date(), "23:59")
            due = timeutil.at(now.date() + timedelta(days=1), base.strftime("%H:%M"))
            tools.REGISTRY["update_task"].fn(s, ctx=ctx, task_id=t.id, due=timeutil.iso(due))
            text = f"📅 ددلاین «{t.title}» شد {timeutil.fa_datetime(due)}."
        if t is not None and action in ("done", "drop", "tomorrow") and prefs.get("auto_replan", True, s=s):
            planner.plan(s, days=7)
    notify.update_meta(message_id, reminder_done=text, undoable=action in ("done", "drop", "tomorrow"),
                       turn_id=ctx.turn_id)
    notify.store_message("assistant", text, "system", kind="chat")
    bus.publish("data_changed", ["tasks", "schedule", "followups"])
    return text


def snooze_message(message_id: int, minutes: int = 60) -> str:
    with session() as s:
        m = s.get(Message, message_id)
        fid = (m.meta or {}).get("followup_id") if m else None
        if not fid:
            return "این پیام یادآوری نیست"
        f = followups.snooze(s, int(fid), minutes)
        at = f.due_at
    text = f"⏰ باشه، {timeutil.fa_time(at)} دوباره یادت می‌ندازم."
    notify.update_meta(message_id, snoozed_until=timeutil.iso(at))
    notify.store_message("assistant", text, "system", kind="chat")
    bus.publish("data_changed", ["followups"])
    return text


def undo_message(message_id: int) -> str:
    with session() as s:
        m = s.get(Message, message_id)
        turn = (m.meta or {}).get("turn_id") if m else None
        already = bool(m and (m.meta or {}).get("undone"))
    if not turn or already:
        return "چیزی برای برگرداندن نیست"
    labels = journal.undo(turn)
    if not labels:
        return "چیزی برای برگرداندن نیست"
    with session() as s:
        if prefs.get("auto_replan", True, s=s) and planner.open_tasks(s):
            planner.plan(s, days=7)
    notify.update_meta(message_id, undone=True, undoable=False)
    text = "↩️ برگردوندم: " + "، ".join(dict.fromkeys(labels))
    notify.store_message("assistant", text, "system", kind="chat", meta={"undo_of": message_id})
    bus.publish("data_changed", ["tasks", "schedule", "followups", "memories", "goals", "habits", "settings", "stats"])
    return text
