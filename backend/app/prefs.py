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
    # reminder = a reminder app that knows your day: what you asked for, deadlines, leaving, after class, morning brief
    # coach = also work-session nudges, idle check-ins, progress check-ins, rescue plans and the evening review
    "followup_style": "reminder",
    "followup_intensity": "normal",  # low | normal | high
    "idle_checkin_hours": 3,
    "post_class_delay_minutes": 15,
    "block_nudges": True,
    "auto_plan_daily": True,
    # planning
    "focus_block_minutes": 90,
    "study_hours": "",  # when the user likes deep work / studying, e.g. "20:00-23:30" or "06:30-08:00,20:00-23:00"
    "min_block_minutes": 30,
    "break_minutes": 15,
    "max_focus_minutes_per_day": 360,
    "class_buffer_minutes": 30,  # used for classes at places without a known travel time
    "prep_minutes": None,  # getting ready before leaving home (None = not asked yet; 30 is assumed)
    "leave_reminders": True,  # "time to get ready / leave" before appointments somewhere else
    "default_task_minutes": 60,
    "daily_focus_goal_minutes": 240,
    "semester_start": None,  # ISO date, used for odd/even week parity
    "auto_replan": True,  # re-plan when tasks change or a block is skipped
    "rescue_mode": True,  # proactive "you're falling behind" help
    "weekly_planning_day": "fri",
    "weekly_planning_time": "17:00",
    # chat sessions: a new conversation starts after this many idle hours (0 = never automatically)
    "session_idle_hours": 6,
    # learning & memory
    "semantic_memory": True,
    "auto_facts": True,
    "adaptive_reminders": True,
    # applying / supervisor search
    "professor_followup_days": 10,
    # IELTS coach
    "ielts_target_band": 7.0,
    "ielts_exam_date": None,
    "vocab_review_time": "13:00",
    "vocab_daily_count": 8,
    # sync, notifications & reports
    "mirror_web_to_telegram": True,
    "voice_replies": "when_voice",  # off | when_voice | always
    "push_enabled": True,
    "employer_name": "",
    "work_report_categories": ["work"],
    # AI cost control & model overrides (empty = value from .env)
    "daily_budget_usd": 0,  # 0 = unlimited
    "llm_chat_model": "",
    "llm_fast_model": "",
    "llm_vision_model": "",
    "llm_ielts_model": "",
    "llm_strong_model": "",
    "llm_fallback_models": "",  # comma separated; '' = FALLBACK_MODELS from .env
    # Google Calendar feed published at /cal/<token>.ics (see calsync.py)
    "ics_include": ["classes", "events", "blocks", "deadlines", "trips"],
}

# cheap models that did best in the evals (python -m app.evals, Nov 2026: deepseek-v4-flash 39/40 at $0.038, the
# lowest cost and fastest). Written once into the settings; a setting is only replaced while it still holds an
# earlier recommendation (or is empty) — a model the user picked in Settings is kept.
MODEL_DEFAULTS_VERSION = 3
RECOMMENDED_MODELS = {
    "llm_chat_model": "deepseek-v4-flash",
    "llm_fast_model": "deepseek-v4-flash",
    "llm_fallback_models": "gpt-4.1-mini,gemini-3.1-flash-lite",
}
EARLIER_RECOMMENDED = {
    "llm_chat_model": {"gemini-3.1-flash-lite"},
    "llm_fast_model": {"deepseek-v4-flash"},
    "llm_fallback_models": {"deepseek-v4-flash,gpt-4.1-mini"},
}

# app state keys (not user preferences)
STATE_KEYS = {"telegram_chat_id", "telegram_user", "telegram_offset", "pairing_code", "last_user_message_at",
              "current_session_id", "model_defaults_version", "gcal_last_sync", "mail_state",
              "quiet_until"}  # do-not-disturb: no follow-ups before this time (only the user's own reminders)
# secrets: not in DEFAULTS, so they are never returned by the settings API nor settable by the model
SECRET_KEYS = {"gcal_ics_urls", "ics_token", "gmail_address", "gmail_app_password"}


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


def apply_model_defaults(s: Session) -> dict[str, str]:
    """One-time switch to the recommended models (see RECOMMENDED_MODELS). Returns what was changed."""
    if int(get("model_defaults_version", 0, s=s) or 0) >= MODEL_DEFAULTS_VERSION:
        return {}
    changed = {}
    chat = get("llm_chat_model", s=s)
    if not chat or chat in EARLIER_RECOMMENDED["llm_chat_model"]:  # the user didn't pick their own chat model
        for key, value in RECOMMENDED_MODELS.items():
            cur = get(key, s=s)
            if (not cur or cur in EARLIER_RECOMMENDED.get(key, ())) and cur != value:
                set(key, value, s=s)
                changed[key] = value
    set("model_defaults_version", MODEL_DEFAULTS_VERSION, s=s)
    return changed
