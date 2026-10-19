"""The conversational agent: one shared conversation for web + Telegram."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import jobs, journal, memory, notify, prefs, timeutil
from ..bus import bus
from ..config import get_settings
from ..db import Message, session
from ..llm import LLMError, get_llm
from . import prompt, tools

log = logging.getLogger(__name__)

MAX_STEPS = 10
HISTORY_LIMIT = memory.HISTORY_WINDOW
lock = asyncio.Lock()

VISION_PROMPT = """You are helping a personal-assistant app. Analyse this image carefully.
If it is a class timetable / weekly schedule (likely an Iranian university schedule in Persian: days شنبه..جمعه,
time slots like 8-10, course names, instructor, room, and sometimes «زوج/فرد» for even/odd weeks), extract EVERY class as
a JSON list inside a ```json block with objects:
{"title", "weekdays": ["sat"|"sun"|"mon"|"tue"|"wed"|"thu"|"fri"], "start_time": "HH:MM", "end_time": "HH:MM",
 "location", "instructor", "week_parity": "all"|"odd"|"even", "notes"}
Read the grid carefully: map each cell to its column/row header (day and time). Merge cells spanning several slots.
Also mention exam dates if present (an exam schedule → list each exam with date/time).
If it is a handwritten or typed English essay, transcribe it exactly (for IELTS grading).
If it is NOT a timetable: describe what it shows in Persian and extract any tasks, deadlines, dates, or homework
mentioned (e.g. a whiteboard with homework, an assignment sheet, a chat screenshot).
Answer in Persian except for the JSON / transcriptions."""

TOOL_LABELS = {
    "add_task": "➕ تسک",
    "update_task": "✏️ ویرایش تسک",
    "complete_task": "✅ انجام شد",
    "delete_task": "🗑 حذف تسک",
    "break_down_task": "🧩 زیرکارها",
    "plan_exam_prep": "📚 برنامه امتحان",
    "move_block": "↔️ جابه‌جایی بلوک",
    "rescue_plan": "🛟 حالت نجات",
    "undo_last": "↩️ برگرداندن",
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
    "add_habit": "🔁 عادت",
    "log_habit": "🔥 ثبت عادت",
    "update_habit": "🔁 ویرایش عادت",
    "delete_habit": "🗑 حذف عادت",
    "add_commitment": "🤝 قول امروز",
    "update_commitment": "🤝 نتیجه قول",
    "grade_ielts_writing": "📝 تصحیح رایتینگ",
    "start_speaking_practice": "🎤 تمرین اسپیکینگ",
    "grade_ielts_speaking": "🎤 نمره اسپیکینگ",
    "log_ielts_score": "📈 نمره آیلتس",
    "add_vocab": "📇 لغت",
    "vocab_review": "📇 مرور لغت",
}
STATUS_LABELS = {
    "grade_ielts_writing": "در حال تصحیح رایتینگ…",
    "grade_ielts_speaking": "در حال بررسی اسپیکینگ…",
    "weekly_report": "در حال نوشتن گزارش…",
    "plan_schedule": "در حال برنامه‌ریزی…",
    "check_availability": "در حال بررسی وقت‌ها…",
    "plan_exam_prep": "در حال چیدن برنامه امتحان…",
}
READ_ONLY = {"get_agenda", "list_tasks", "list_recurring_events", "get_stats", "list_followups", "list_habits",
             "list_commitments", "ielts_progress", "vocab_due", "get_insights", "rescue_plan", "check_availability"}

DeltaCallback = Callable[[str], Awaitable[None]]


@dataclass
class Attachment:
    kind: str  # image | audio | document
    data: bytes
    mime: str
    filename: str = ""


def _summary(name: str, args: dict[str, Any], result: dict[str, Any]) -> str:
    label = TOOL_LABELS.get(name, name)
    if result.get("needs_confirmation"):
        return f"{label}: منتظر تأیید"
    if "error" in result:
        return f"{label}: ⚠️"
    try:
        if name in ("add_task", "update_task", "complete_task"):
            return f"{label}: {result['task']['title']}"
        if name == "break_down_task":
            return f"{label}: {timeutil.fa_digits(len(result.get('subtasks', [])))} مرحله"
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
        if name in ("add_habit", "log_habit", "update_habit"):
            h = result["habit"]
            return f"{label}: {h['title']}" + (f" 🔥{timeutil.fa_digits(h['streak'])}" if h.get("streak") else "")
        if name in ("grade_ielts_writing", "grade_ielts_speaking"):
            return f"{label}: {timeutil.fa_digits(result.get('overall'))}"
        if name == "add_vocab":
            return f"{label}: {timeutil.fa_digits(len(result.get('added', [])))} لغت"
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
            if meta.get("forwarded_from"):
                content = f"[پیام فورواردشده از «{meta['forwarded_from']}»]\n" + content
            if meta.get("image_analysis"):
                content += "\n\n[تصویر پیوست — تحلیل خودکار]\n" + meta["image_analysis"]
            if meta.get("document_text"):
                content += f"\n\n[فایل پیوست: {meta.get('document_name', '')}]\n" + meta["document_text"][:3000]
            out.append({"role": "user", "content": content or "(پیام خالی)"})
        else:
            notes = meta.get("action_log")
            if notes:
                content += "\n\n[actions done: " + "; ".join(notes) + "]"
            if meta.get("undone"):
                content += "\n[these actions were later undone by the user]"
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
    if result.get("needs_confirmation"):
        return f"{name} waiting for user confirmation ({result.get('what', '')})"
    if "error" in result:
        return f"{name} failed: {result['error']}"
    ids = []
    for key in ("task", "event", "followup", "memory", "goal", "recurring_event", "habit", "commitment", "project"):
        if isinstance(result.get(key), dict) and "id" in result[key]:
            ids.append(f"{key}#{result[key]['id']}")
    if name == "add_recurring_events":
        ids += [f"class#{c['id']} {c['title']}" for c in result.get("created", [])]
    if name == "break_down_task":
        ids += [f"subtask#{t['id']}" for t in result.get("subtasks", [])]
    if name == "plan_schedule":
        ids.append(f"{len(result.get('blocks', []))} blocks")
    return f"{name}({', '.join(ids)})" if ids else name


async def _context_block(query: str) -> str:
    """Summaries of older conversation + semantically related older messages/memories."""
    parts = []
    sums = memory.latest_summaries(2)
    if sums:
        parts.append("## Earlier in our conversation (summaries)\n" + "\n\n".join(x.content for x in sums))
    try:
        hits = await memory.recall(query)
    except Exception:  # noqa: BLE001
        log.exception("recall failed")
        hits = []
    if hits:
        parts.append(memory.format_recall(hits))
    return "\n\n".join(parts)


class _Throttle:
    def __init__(self, fn: DeltaCallback | None, every: float) -> None:
        self.fn, self.every, self.last = fn, every, 0.0

    async def __call__(self, text: str) -> None:
        now = time.monotonic()
        if self.fn is None or now - self.last < self.every:
            return
        self.last = now
        await self.fn(text)


async def run_agent(
    user_content: str | list[dict[str, Any]],
    channel: str,
    exclude_id: int | None = None,
    turn_id: str | None = None,
    model: str | None = None,
    on_delta: DeltaCallback | None = None,
    on_status: DeltaCallback | None = None,
) -> tuple[str, list[dict[str, Any]], set[str]]:
    llm = get_llm()
    with session() as s:
        system = prompt.system_prompt(s)
    query = user_content if isinstance(user_content, str) else json.dumps(user_content, ensure_ascii=False)
    extra = await _context_block(query[:2000])
    if extra:
        system += "\n\n" + extra
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages += _history_messages(exclude_id=exclude_id)
    messages.append({"role": "user", "content": user_content})
    ctx = tools.Ctx(channel=channel, turn_id=turn_id)
    actions: list[dict[str, Any]] = []
    schemas = tools.schemas()
    stream = _Throttle(on_delta, 0.12) if on_delta else None
    try:
        for _ in range(MAX_STEPS):
            res = await llm.chat(messages, tools=schemas, model=model, on_delta=stream)
            if not res.tool_calls:
                return res.content.strip(), actions, ctx.changed
            messages.append(res.raw_message)
            for call in res.tool_calls:
                try:
                    args = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if on_status is not None:
                    await on_status(STATUS_LABELS.get(call.name) or TOOL_LABELS.get(call.name, "…"))
                result = await tools.execute(call.name, call.arguments, ctx)
                actions.append(
                    {
                        "tool": call.name,
                        "ok": "error" not in result and not result.get("needs_confirmation"),
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
        res = await llm.chat(messages, model=model, on_delta=stream)
        return res.content.strip(), actions, ctx.changed
    finally:
        tools.finalize(ctx)


def _save_upload(att: Attachment) -> str:
    folder = Path(get_settings().upload_dir)
    folder.mkdir(parents=True, exist_ok=True)
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf"}.get(
        att.mime, Path(att.filename).suffix or ".bin"
    )
    name = f"{timeutil.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{ext}"
    (folder / name).write_bytes(att.data)
    return f"/uploads/{name}"


def extract_document_text(att: Attachment, max_chars: int = 20000) -> str:
    name = (att.filename or "").lower()
    if att.mime == "application/pdf" or name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(att.data))
        pages = []
        for i, page in enumerate(reader.pages[:40]):
            txt = (page.extract_text() or "").strip()
            if txt:
                pages.append(f"--- صفحه {i + 1} ---\n{txt}")
            if sum(len(p) for p in pages) > max_chars:
                break
        text = "\n".join(pages)
        if not text.strip():
            return "(متن قابل استخراج نیست — احتمالاً PDF اسکن‌شده است؛ عکس صفحه‌ها را بفرست)"
        return text[:max_chars]
    for enc in ("utf-8", "utf-16", "cp1256"):
        try:
            return att.data.decode(enc)[:max_chars]
        except UnicodeDecodeError:
            continue
    return "(فایل خوانا نیست)"


def _speaking_mode() -> dict[str, Any] | None:
    sp = prefs.get("speaking_practice")
    if sp and sp.get("until") and timeutil.parse_datetime(sp["until"]) > timeutil.now():
        return sp
    return None


async def handle_user_message(
    text: str,
    channel: str,
    attachments: list[Attachment] | None = None,
    meta: dict[str, Any] | None = None,
    on_delta: DeltaCallback | None = None,
    on_status: DeltaCallback | None = None,
    model: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Process one user turn. Returns (user_message, assistant_message) dicts."""
    attachments = attachments or []
    meta = dict(meta or {})
    llm = get_llm()
    turn_id = uuid.uuid4().hex[:16]

    async def web_delta(t: str) -> None:
        bus.publish("stream", {"turn_id": turn_id, "text": t})
        if on_delta is not None:
            await on_delta(t)

    async def web_status(label: str) -> None:
        bus.publish("stream", {"turn_id": turn_id, "status": label})
        if on_status is not None:
            await on_status(label)

    async with lock:
        bus.publish("typing", True)
        try:
            content = (text or "").strip()
            analyses: list[str] = []
            files = []
            speaking = _speaking_mode()
            for att in attachments:
                if att.kind == "audio":
                    try:
                        transcript = await llm.transcribe(att.data, att.filename or "voice.ogg",
                                                          language="en" if speaking else "fa")
                    except TypeError:  # simple fakes in tests
                        transcript = await llm.transcribe(att.data, att.filename or "voice.ogg")
                    except LLMError as e:
                        transcript = ""
                        meta["voice_error"] = str(e)
                    meta["voice"] = True
                    if speaking:
                        meta["speaking_answer"] = True
                    content = (content + "\n" + transcript).strip()
                elif att.kind == "image":
                    url = _save_upload(att)
                    files.append({"type": "image", "url": url})
                    try:
                        analyses.append(await llm.vision(att.data, att.mime, VISION_PROMPT + (f"\nUser caption: {text}" if text else "")))
                    except LLMError as e:
                        analyses.append(f"(تحلیل تصویر ناموفق بود: {e})")
                elif att.kind == "document":
                    url = _save_upload(att)
                    files.append({"type": "file", "url": url, "name": att.filename or "file"})
                    try:
                        meta["document_text"] = extract_document_text(att)
                    except Exception as e:  # noqa: BLE001
                        meta["document_text"] = f"(خواندن فایل ناموفق بود: {e})"
                    meta["document_name"] = att.filename
            if files:
                meta["attachments"] = files
            if analyses:
                meta["image_analysis"] = "\n\n".join(analyses)
            if not content and meta.get("voice") and not files:
                content = "(ویس نامفهوم بود)"
            user_msg = notify.store_message("user", content, channel, meta=meta)

            llm_content = content or "(کاربر فقط یک فایل/تصویر فرستاد)"
            if meta.get("forwarded_from"):
                llm_content = f"[پیام فورواردشده از «{meta['forwarded_from']}» — احتمالاً اطلاعیه/تکلیف]\n" + llm_content
            if analyses:
                llm_content += "\n\n[تصویر پیوست — تحلیل خودکار]\n" + meta["image_analysis"]
            if meta.get("document_text"):
                llm_content += f"\n\n[فایل پیوست: {meta.get('document_name', '')}]\n" + meta["document_text"]
            if meta.get("speaking_answer") and speaking:
                llm_content = (f"[IELTS speaking practice answer (voice transcript) to: «{speaking.get('question')}» "
                               f"part {speaking.get('part')} — grade it with grade_ielts_speaking]\n") + llm_content
            if meta.get("voice_error"):
                llm_content += f"\n\n[system: voice transcription failed: {meta['voice_error']}]"
            try:
                reply, actions, changed = await run_agent(
                    llm_content, channel, exclude_id=user_msg["id"], turn_id=turn_id, model=model,
                    on_delta=web_delta, on_status=web_status,
                )
            except LLMError as e:
                reply, actions, changed = f"⚠️ الان به مدل زبانی دسترسی ندارم.\n`{e}`", [], set()
            if not reply:
                reply = "👌"
            assistant_meta = {
                "actions": [{"label": a["label"], "ok": a["ok"], "tool": a["tool"]} for a in actions if a["visible"]],
                "action_log": [a["log"] for a in actions if a["tool"] not in READ_ONLY],
                "turn_id": turn_id,
                "undoable": journal.has_changes(turn_id),
            }
            assistant_msg = notify.store_message("assistant", reply, channel, meta=assistant_meta)
            if changed:
                bus.publish("data_changed", sorted(changed))
            jobs.after_turn(user_msg["id"])
            return user_msg, assistant_msg
        finally:
            bus.publish("typing", False)


async def compose(instruction: str, fallback: str) -> str:
    """Write a proactive message (no tools) in the assistant's voice."""
    llm = get_llm()
    try:
        with session() as s:
            system = prompt.system_prompt(s)
        sums = memory.latest_summaries(1)
        if sums:
            system += "\n\n## Earlier in our conversation (summary)\n" + sums[0].content
        messages = [{"role": "system", "content": system}]
        messages += _history_messages(limit=12)
        messages.append(
            {
                "role": "user",
                "content": "[SYSTEM TRIGGER — this is not written by the user. Write the next proactive message to the "
                "user now, in Persian, short (max ~6 lines unless it's a daily brief/review/weekly planning), natural "
                "and specific, no preamble like 'سلام دوباره' every time, do not mention this trigger.]\n\n" + instruction,
            }
        )
        res = await llm.chat(messages, model=llm.model("fast") if hasattr(llm, "model") else None, purpose="compose")
        text = res.content.strip()
        return text or fallback
    except LLMError as e:
        log.warning("compose failed, using fallback: %s", e)
        return fallback
