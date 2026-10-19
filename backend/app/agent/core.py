"""The conversational agent: one shared conversation for web + Telegram."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import notify, timeutil
from ..bus import bus
from ..config import get_settings
from ..db import Message, session
from ..llm import LLMError, get_llm
from . import prompt, tools

log = logging.getLogger(__name__)

MAX_STEPS = 10
HISTORY_LIMIT = 30
lock = asyncio.Lock()

VISION_PROMPT = """You are helping a personal-assistant app. Analyse this image carefully.
If it is a class timetable / weekly schedule (likely an Iranian university schedule in Persian: days شنبه..جمعه,
time slots like 8-10, course names, instructor, room, and sometimes «زوج/فرد» for even/odd weeks), extract EVERY class as
a JSON list inside a ```json block with objects:
{"title", "weekdays": ["sat"|"sun"|"mon"|"tue"|"wed"|"thu"|"fri"], "start_time": "HH:MM", "end_time": "HH:MM",
 "location", "instructor", "week_parity": "all"|"odd"|"even", "notes"}
Read the grid carefully: map each cell to its column/row header (day and time). Merge cells spanning several slots.
Also mention exam dates if present.
If it is NOT a timetable: describe what it shows in Persian and extract any tasks, deadlines, dates, or homework
mentioned (e.g. a whiteboard with homework, an assignment sheet, a chat screenshot).
Answer in Persian except for the JSON."""

TOOL_LABELS = {
    "add_task": "➕ تسک",
    "update_task": "✏️ ویرایش تسک",
    "complete_task": "✅ انجام شد",
    "delete_task": "🗑 حذف تسک",
    "add_recurring_events": "🗓 کلاس",
    "update_recurring_event": "🗓 ویرایش کلاس",
    "delete_recurring_event": "🗑 حذف کلاس",
    "add_event": "📌 رویداد",
    "update_event": "📌 ویرایش رویداد",
    "delete_event": "🗑 حذف رویداد",
    "check_availability": "🔎 بررسی وقت",
    "plan_schedule": "🧭 برنامه‌ریزی",
    "log_work": "⏱ ثبت زمان",
    "start_focus": "▶️ شروع تمرکز",
    "stop_focus": "⏹ پایان تمرکز",
    "schedule_followup": "⏰ یادآوری",
    "cancel_followup": "🔕 لغو یادآوری",
    "remember": "🧠 به خاطر سپردم",
    "forget": "🧹 فراموش شد",
    "add_goal": "🎯 هدف",
    "update_goal": "🎯 ویرایش هدف",
    "weekly_report": "📄 گزارش هفتگی",
    "update_preferences": "⚙️ تنظیمات",
}


@dataclass
class Attachment:
    kind: str  # image | audio
    data: bytes
    mime: str
    filename: str = ""


def _summary(name: str, args: dict[str, Any], result: dict[str, Any]) -> str:
    label = TOOL_LABELS.get(name, name)
    if "error" in result:
        return f"{label}: ⚠️"
    try:
        if name == "add_task":
            return f"{label}: {result['task']['title']}"
        if name in ("update_task", "complete_task"):
            return f"{label}: {result['task']['title']}"
        if name == "add_recurring_events":
            created = result.get("created", [])
            return f"{label}: {created[0]['title']}" if len(created) == 1 else f"{label}: {timeutil.fa_digits(len(created))} مورد"
        if name in ("add_event", "update_event"):
            return f"{label}: {result['event']['title']}"
        if name == "plan_schedule":
            return f"{label}: {timeutil.fa_digits(len(result.get('blocks', [])))} بلوک"
        if name in ("log_work", "stop_focus"):
            return f"{label}: {timeutil.fa_duration(result.get('logged_minutes', 0))}"
        if name == "schedule_followup":
            due = timeutil.parse_datetime(result["followup"]["due"])
            return f"{label}: {timeutil.fa_datetime(due)}"
        if name == "remember":
            return f"{label}"
    except Exception:  # noqa: BLE001
        pass
    return label


def _history_messages(limit: int = HISTORY_LIMIT, exclude_id: int | None = None) -> list[dict[str, Any]]:
    with session() as s:
        rows = s.query(Message).order_by(Message.id.desc()).limit(limit + 1).all()
    rows = [r for r in reversed(rows) if r.id != exclude_id][-limit:]
    out: list[dict[str, Any]] = []
    for m in rows:
        content = m.content or ""
        meta = m.meta or {}
        if m.role == "user":
            if meta.get("image_analysis"):
                content += "\n\n[تصویر پیوست — تحلیل خودکار]\n" + meta["image_analysis"]
            out.append({"role": "user", "content": content or "(پیام خالی)"})
        else:
            notes = meta.get("action_log")
            if notes:
                content += "\n\n[actions done: " + "; ".join(notes) + "]"
            if m.kind != "chat":
                content = f"[proactive {m.kind} message you sent]\n" + content
            out.append({"role": "assistant", "content": content})
    # most chat APIs want the conversation to start with a user turn and alternate roles
    while out and out[0]["role"] != "user":
        out.pop(0)
    merged: list[dict[str, Any]] = []
    for msg in out:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1] = {**merged[-1], "content": merged[-1]["content"] + "\n\n" + msg["content"]}
        else:
            merged.append(msg)
    return merged


def _action_log(name: str, args: dict[str, Any], result: dict[str, Any]) -> str:
    """Compact machine-readable trace kept in history so the model remembers ids."""
    if "error" in result:
        return f"{name} failed: {result['error']}"
    ids = []
    for key in ("task", "event", "followup", "memory", "goal", "recurring_event"):
        if isinstance(result.get(key), dict) and "id" in result[key]:
            ids.append(f"{key}#{result[key]['id']}")
    if name == "add_recurring_events":
        ids += [f"class#{c['id']} {c['title']}" for c in result.get("created", [])]
    if name == "plan_schedule":
        ids.append(f"{len(result.get('blocks', []))} blocks")
    return f"{name}({', '.join(ids)})" if ids else name


async def run_agent(user_content: str | list[dict[str, Any]], channel: str, exclude_id: int | None = None,
                    ) -> tuple[str, list[dict[str, Any]], set[str]]:
    llm = get_llm()
    with session() as s:
        system = prompt.system_prompt(s)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages += _history_messages(exclude_id=exclude_id)
    messages.append({"role": "user", "content": user_content})
    ctx = tools.Ctx(channel=channel)
    actions: list[dict[str, Any]] = []
    schemas = tools.schemas()
    for _ in range(MAX_STEPS):
        res = await llm.chat(messages, tools=schemas)
        if not res.tool_calls:
            return res.content.strip(), actions, ctx.changed
        messages.append(res.raw_message)
        for call in res.tool_calls:
            try:
                args = json.loads(call.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = await tools.execute(call.name, call.arguments, ctx)
            actions.append(
                {
                    "tool": call.name,
                    "ok": "error" not in result,
                    "label": _summary(call.name, args, result),
                    "log": _action_log(call.name, args, result),
                    "visible": call.name in TOOL_LABELS,
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str)[:14000],
                }
            )
        if ctx.changed:
            bus.publish("data_changed", sorted(ctx.changed))
    messages.append({"role": "user", "content": "[system] Stop calling tools now and reply to the user."})
    res = await llm.chat(messages)
    return res.content.strip(), actions, ctx.changed


def _save_upload(att: Attachment) -> str:
    folder = Path(get_settings().upload_dir)
    folder.mkdir(parents=True, exist_ok=True)
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(att.mime, Path(att.filename).suffix or ".bin")
    name = f"{timeutil.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{ext}"
    (folder / name).write_bytes(att.data)
    return f"/uploads/{name}"


async def handle_user_message(text: str, channel: str, attachments: list[Attachment] | None = None,
                              meta: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Process one user turn. Returns (user_message, assistant_message) dicts."""
    attachments = attachments or []
    meta = dict(meta or {})
    llm = get_llm()
    async with lock:
        bus.publish("typing", True)
        try:
            content = (text or "").strip()
            analyses: list[str] = []
            images = []
            for att in attachments:
                if att.kind == "audio":
                    try:
                        transcript = await llm.transcribe(att.data, att.filename or "voice.ogg")
                    except LLMError as e:
                        transcript = ""
                        meta["voice_error"] = str(e)
                    meta["voice"] = True
                    content = (content + "\n" + transcript).strip()
                elif att.kind == "image":
                    url = _save_upload(att)
                    images.append({"type": "image", "url": url})
                    try:
                        analyses.append(await llm.vision(att.data, att.mime, VISION_PROMPT + (f"\nUser caption: {text}" if text else "")))
                    except LLMError as e:
                        analyses.append(f"(تحلیل تصویر ناموفق بود: {e})")
            if images:
                meta["attachments"] = images
            if analyses:
                meta["image_analysis"] = "\n\n".join(analyses)
            if not content and meta.get("voice") and not images:
                content = "(ویس نامفهوم بود)"
            user_msg = notify.store_message("user", content, channel, meta=meta)

            llm_content = content or "(کاربر فقط یک تصویر فرستاد)"
            if analyses:
                llm_content += "\n\n[تصویر پیوست — تحلیل خودکار]\n" + meta["image_analysis"]
            if meta.get("voice_error"):
                llm_content += f"\n\n[system: voice transcription failed: {meta['voice_error']}]"
            try:
                reply, actions, changed = await run_agent(llm_content, channel, exclude_id=user_msg["id"])
            except LLMError as e:
                reply, actions, changed = f"⚠️ الان به مدل زبانی دسترسی ندارم.\n`{e}`", [], set()
            if not reply:
                reply = "👌"
            assistant_meta = {
                "actions": [{"label": a["label"], "ok": a["ok"], "tool": a["tool"]} for a in actions if a["visible"]],
                "action_log": [a["log"] for a in actions if a["tool"] not in ("get_agenda", "list_tasks")],
            }
            assistant_msg = notify.store_message("assistant", reply, channel, meta=assistant_meta)
            if changed:
                bus.publish("data_changed", sorted(changed))
            return user_msg, assistant_msg
        finally:
            bus.publish("typing", False)


async def compose(instruction: str, fallback: str) -> str:
    """Write a proactive message (no tools) in the assistant's voice."""
    llm = get_llm()
    try:
        with session() as s:
            system = prompt.system_prompt(s)
        messages = [{"role": "system", "content": system}]
        messages += _history_messages(limit=12)
        messages.append(
            {
                "role": "user",
                "content": "[SYSTEM TRIGGER — this is not written by the user. Write the next proactive message to the "
                "user now, in Persian, short (max ~6 lines unless it's a daily brief/review), natural and specific, "
                "no preamble like 'سلام دوباره' every time, do not mention this trigger.]\n\n" + instruction,
            }
        )
        res = await llm.chat(messages, model=get_settings().fast_model)
        text = res.content.strip()
        return text or fallback
    except LLMError as e:
        log.warning("compose failed, using fallback: %s", e)
        return fallback
