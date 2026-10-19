"""User preferences stored in the KV table, with sane defaults."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .db import KV, session

DEFAULTS: dict[str, Any] = {
    "user_name": "",
    "assistant_name": "پیگیر",
    # daily rhythm
    "wake_time": "07:30",
    "sleep_time": "00:30",
    "work_start": "09:00",  # productive window used by the planner
    "work_end": "22:30",
    # proactive messages
    "daily_brief_time": "08:00",
    "daily_review_time": "22:45",
    "weekly_report_day": "thu",
    "weekly_report_time": "18:00",
    "followup_intensity": "normal",  # low | normal | high
    "idle_checkin_hours": 3,
    "post_class_delay_minutes": 15,
    "block_nudges": True,
    "auto_plan_daily": True,
    # planning
    "focus_block_minutes": 90,
    "min_block_minutes": 30,
    "break_minutes": 15,
    "max_focus_minutes_per_day": 360,
    "class_buffer_minutes": 30,
    "default_task_minutes": 60,
    "daily_focus_goal_minutes": 240,
    "semester_start": None,  # ISO date, used for odd/even week parity
    # sync & reports
    "mirror_web_to_telegram": True,
    "employer_name": "",
    "work_report_categories": ["work"],
}

# app state keys (not user preferences)
STATE_KEYS = {"telegram_chat_id", "telegram_user", "telegram_offset", "pairing_code", "last_user_message_at"}


def get(key: str, default: Any = None, s: Session | None = None) -> Any:
    if s is None:
        with session() as s2:
            return get(key, default, s2)
    row = s.get(KV, key)
    if row is None:
        return DEFAULTS.get(key, default)
    return row.value


def set(key: str, value: Any, s: Session | None = None) -> None:  # noqa: A001 - module-level API
    if s is None:
        with session() as s2:
            return set(key, value, s2)
    row = s.get(KV, key)
    if row is None:
        s.add(KV(key=key, value=value))
    else:
        row.value = value
    s.flush()


def all_prefs(s: Session | None = None) -> dict[str, Any]:
    if s is None:
        with session() as s2:
            return all_prefs(s2)
    out = dict(DEFAULTS)
    for row in s.query(KV).filter(KV.key.in_(list(DEFAULTS))).all():
        out[row.key] = row.value
    return out
