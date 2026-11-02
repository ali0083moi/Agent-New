"""Changes waiting for the user's ✅/❌ — shown as buttons on the web and in Telegram.

Deletions (and bundles the assistant proposes with `ask_approval`) are stored as concrete tool calls. Pressing ✅
runs them right away with the same tool code, without asking the LLM again — so an approved change can't get lost
in the conversation. A typed «آره» / «نه» right after such a message does the same.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from . import notify, timeutil
from .bus import bus
from .db import Message, PendingAction, session

log = logging.getLogger(__name__)

TTL = timedelta(hours=24)

_YES = {"آره", "اره", "آری", "بله", "بلی", "باشه", "اوکی", "اوکیه", "ok", "okay", "yes", "yep", "yeah", "y", "حتما", "حتماً",
        "تایید", "تأیید", "تاییده", "تأییده", "قبول", "موافقم", "انجام بده", "انجامش بده", "بکن", "بکنش", "بزن", "برو",
        "پاک کن", "پاکش کن", "حذف کن", "حذفش کن", "درسته", "اوهوم", "آها", "👍", "✅", "دقیقا", "دقیقاً"}
_NO = {"نه", "نخیر", "نکن", "نه نکن", "ولش کن", "بیخیال", "بی خیال", "لغو", "کنسل", "no", "nope", "n", "❌", "دست نزن",
       "نه ممنون", "فعلا نه", "فعلاً نه", "لازم نیست", "نمیخواد", "نمی‌خواد"}
_BUT = re.compile(r"ولی|اما|فقط|به ?جز|بجز|به‌جز|غیر از|منتها|but|except|only")


def _norm(text: str) -> str:
    t = timeutil.to_latin_digits(text or "").strip().lower().replace("‌", " ")
    t = re.sub(r"[!.؟?،,]+", " ", t)
    t = t.replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"\s+", " ", t).strip()


def quick_answer(text: str) -> bool | None:
    """True / False for a bare yes / no ("آره", "آره حذفش کن", "نه ولش کن"); None for anything else."""
    t = _norm(text)
    if not t or len(t) > 40 or _BUT.search(t):
        return None
    if t in _YES or t in _NO:
        return t in _YES
    words = t.split()
    if len(words) > 4:
        return None
    first = words[0]
    rest = " ".join(words[1:])
    if first in _YES and (not rest or rest in _YES or rest in {"ممنون", "مرسی", "لطفا", "لطفاً", "دمت گرم"} or re.fullmatch(r"(حذف|پاک|انجام|ثبت|بکن|بزن|برو|عوض|تغییر)\S*( (کن|بده|بکن|شه|بشه))?", rest)):
        return True
    if first in _NO and (not rest or rest in _NO or rest in {"ممنون", "مرسی", "فعلا", "فعلاً", "نکن", "دست نزن"}):
        return False
    return None


# ---------------------------------------------------------------- creating
def create(s: Session, turn_id: str | None, tool: str, args: dict[str, Any], what: str, key: str = "",
           summary: str = "") -> PendingAction:
    """Store one proposed call. Older proposals the user never answered are dropped (the new one replaces them)."""
    turn_id = turn_id or ""
    for old in s.query(PendingAction).filter(PendingAction.status == "pending", PendingAction.turn_id != turn_id).all():
        old.status, old.resolved_at = "expired", timeutil.now()
    pa = PendingAction(turn_id=turn_id, tool=tool, args=dict(args), what=what, key=key, summary=summary)
    s.add(pa)
    s.flush()
    return pa


def consume(s: Session, key: str, turn_id: str | None) -> bool:
    """A later turn re-ran the tool with confirmed=true after the user typed their approval: allowed once."""
    if not key:
        return False
    pa = (s.query(PendingAction)
          .filter(PendingAction.key == key, PendingAction.status == "pending", PendingAction.turn_id != (turn_id or ""))
          .order_by(PendingAction.id.desc()).first())
    if pa is None or timeutil.now() - pa.created_at > TTL:
        return False
    pa.status, pa.resolved_at = "approved", timeutil.now()
    return True


def for_turn(s: Session, turn_id: str) -> list[PendingAction]:
    return (s.query(PendingAction).filter(PendingAction.turn_id == turn_id, PendingAction.status == "pending")
            .order_by(PendingAction.id).all())


def turn_meta(turn_id: str) -> dict[str, Any] | None:
    """What the message shows: the summary line and the items waiting for the buttons."""
    with session() as s:
        rows = for_turn(s, turn_id)
        if not rows:
            return None
        summary = next((r.summary for r in rows if r.summary), "")
        return {"status": "pending", "summary": summary, "items": [r.what for r in rows]}


def attach(turn_id: str, message_id: int) -> None:
    with session() as s:
        for r in for_turn(s, turn_id):
            r.message_id = message_id


def waiting_message_id(s: Session, session_id: int | None) -> int | None:
    """The latest assistant message in this chat session, if it still waits for ✅/❌."""
    q = s.query(Message).filter(Message.role == "assistant")
    if session_id is not None:
        q = q.filter(Message.session_id == session_id)
    m = q.order_by(Message.id.desc()).first()
    if m is None or (m.meta or {}).get("confirm", {}).get("status") != "pending":
        return None
    if not s.query(PendingAction.id).filter(PendingAction.message_id == m.id, PendingAction.status == "pending").first():
        return None
    return m.id


def context_lines(s: Session) -> list[str]:
    rows = (s.query(PendingAction).filter(PendingAction.status == "pending",
                                          PendingAction.created_at >= timeutil.now() - TTL)
            .order_by(PendingAction.id).limit(8).all())
    if not rows:
        return []
    return ["\n## Waiting for the user's ✅/❌ button (already shown; don't ask again, don't redo these)"] + [
        f"- {r.what}" for r in rows]


# ---------------------------------------------------------------- answering
async def resolve(message_id: int, approve: bool, channel: str = "web") -> dict[str, Any]:
    """Run (or drop) everything a message proposed. Returns the stored confirmation message.

    The caller holds the agent lock (agent.core.lock) so this never runs in the middle of a turn."""
    from .agent import core, tools

    with session() as s:
        m = s.get(Message, message_id)
        rows = (s.query(PendingAction).filter(PendingAction.message_id == message_id, PendingAction.status == "pending")
                .order_by(PendingAction.id).all())
        calls = [(r.id, r.tool, dict(r.args or {}), r.what, r.turn_id, r.created_at) for r in rows]
        session_id = m.session_id if m is not None else None
        turn_id = (m.meta or {}).get("turn_id") if m is not None else None
    if not calls:
        return {"ok": False, "text": "این درخواست قبلاً جواب داده شده یا دیگر معتبر نیست."}
    now = timeutil.now()
    if approve and now - calls[0][5] > TTL:
        _close([c[0] for c in calls], "expired")
        notify.update_meta(message_id, confirm=_confirm_state(message_id, "expired"))
        return {"ok": False, "text": "این پیشنهاد قدیمی شده؛ دوباره بگو تا از نو بررسی کنم."}

    if not approve:
        _close([c[0] for c in calls], "rejected")
        notify.update_meta(message_id, confirm=_confirm_state(message_id, "rejected"))
        msg = notify.store_message(
            "assistant", "باشه، دست نزدم.", channel if channel != "web" else "system", kind="chat",
            meta={"action_log": ["user pressed ❌ — not done: " + "; ".join(c[3] for c in calls)], "reply_to": message_id},
            session_id=session_id)
        return {"ok": True, "text": msg["content"], "message": msg}

    ctx = tools.Ctx(channel=channel, turn_id=turn_id or calls[0][4] or None, trusted=True)
    done, failed, logs = [], [], []
    for pid, name, args, what, _, _ in calls:
        try:
            result = await tools.execute(name, args, ctx)
        except Exception as e:  # noqa: BLE001
            log.exception("pending action %s failed", pid)
            result = {"error": str(e)}
        ok = "error" not in result and not result.get("needs_confirmation")
        _close([pid], "approved" if ok else "failed", result)
        logs.append(core._action_log(name, args, result))
        (done if ok else failed).append(what if ok else f"{what} ({result.get('error', '')})")
    tools.finalize(ctx)
    if ctx.changed:
        bus.publish("data_changed", sorted(ctx.changed))
    from . import journal

    notify.update_meta(message_id, confirm=_confirm_state(message_id, "approved" if done else "failed"),
                       undoable=bool(ctx.turn_id and journal.has_changes(ctx.turn_id)))
    lines = [f"✅ انجام شد: {x}" for x in done] + [f"⚠️ نشد: {x}" for x in failed]
    msg = notify.store_message(
        "assistant", "\n".join(lines), channel if channel != "web" else "system", kind="chat",
        meta={"action_log": ["after the user pressed ✅: " + "; ".join(logs)], "reply_to": message_id,
              "actions": [{"label": x, "ok": True, "tool": "approved"} for x in done]},
        session_id=session_id)
    return {"ok": not failed, "text": msg["content"], "message": msg}


def _close(ids: list[int], status: str, result: dict[str, Any] | None = None) -> None:
    with session() as s:
        for r in s.query(PendingAction).filter(PendingAction.id.in_(ids)).all():
            r.status, r.resolved_at = status, timeutil.now()
            if result is not None:
                r.result = {k: v for k, v in result.items() if k in ("error", "warnings")} or {"ok": True}


def _confirm_state(message_id: int, status: str) -> dict[str, Any]:
    with session() as s:
        m = s.get(Message, message_id)
        cur = dict((m.meta or {}).get("confirm") or {}) if m is not None else {}
    cur["status"] = status
    cur["at"] = timeutil.iso(timeutil.now())
    return cur
