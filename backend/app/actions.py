"""User actions on a message that don't need the LLM: snooze a reminder, undo a turn."""

from __future__ import annotations

from . import followups, journal, notify, planner, prefs, timeutil
from .bus import bus
from .db import Message, session


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
