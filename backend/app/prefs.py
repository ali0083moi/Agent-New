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
    "llm_fallback_models": "",  # comma separated; '' = FALLBACK_MODELS from .env
}

# cheap models that did best in the evals (python -m app.evals, Oct 2026): written once into the settings of
# installs that still use the old defaults from .env, unless the user already picked models in Settings
MODEL_DEFAULTS_VERSION = 2
RECOMMENDED_MODELS = {
    "llm_chat_model": "gemini-3.1-flash-lite",
    "llm_fast_model": "deepseek-v4-flash",
    "llm_fallback_models": "deepseek-v4-flash,gpt-4.1-mini",
}

# app state keys (not user preferences)
STATE_KEYS = {"telegram_chat_id", "telegram_user", "telegram_offset", "pairing_code", "last_user_message_at",
              "current_session_id", "model_defaults_version"}


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
    if not get("llm_chat_model", s=s) and not get("llm_fast_model", s=s):
        for key, value in RECOMMENDED_MODELS.items():
            if not get(key, s=s):
                set(key, value, s=s)
                changed[key] = value
    set("model_defaults_version", MODEL_DEFAULTS_VERSION, s=s)
    return changed
