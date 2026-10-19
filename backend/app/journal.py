"""Undo support: snapshot rows before the assistant changes them, revert a whole turn later.

Also hosts the confirmation guard for destructive actions: the first delete request only
returns `needs_confirmation`; the delete happens when the call is repeated with
confirmed=true in a *later* turn (i.e. after the user actually answered).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from . import prefs
from .db import Base, Journal, session

log = logging.getLogger(__name__)


def _models() -> dict[str, type]:
    return {m.class_.__tablename__: m.class_ for m in Base.registry.mappers}


def _dump(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"__dt": value.isoformat()}
    if isinstance(value, date):
        return {"__d": value.isoformat()}
    if isinstance(value, bytes):
        return None
    return value


def _load(value: Any) -> Any:
    if isinstance(value, dict) and "__dt" in value:
        return datetime.fromisoformat(value["__dt"])
    if isinstance(value, dict) and "__d" in value:
        return date.fromisoformat(value["__d"])
    return value


def snapshot(obj: Any) -> dict[str, Any]:
    return {c.key: _dump(getattr(obj, c.key)) for c in obj.__table__.columns}


def _turn(ctx: Any) -> str | None:
    return getattr(ctx, "turn_id", None)


def created(s: Session, ctx: Any, obj: Any, label: str = "") -> None:
    if _turn(ctx):
        s.flush()
        s.add(Journal(turn_id=ctx.turn_id, op="create", table=obj.__tablename__, row_id=obj.id, label=label))


def before_update(s: Session, ctx: Any, obj: Any, label: str = "") -> None:
    if _turn(ctx):
        s.add(Journal(turn_id=ctx.turn_id, op="update", table=obj.__tablename__, row_id=obj.id,
                      before=snapshot(obj), label=label))


def before_delete(s: Session, ctx: Any, obj: Any, label: str = "") -> None:
    if _turn(ctx):
        s.add(Journal(turn_id=ctx.turn_id, op="delete", table=obj.__tablename__, row_id=obj.id,
                      before=snapshot(obj), label=label))


def pref_change(s: Session, ctx: Any, key: str, old: Any) -> None:
    if _turn(ctx):
        s.add(Journal(turn_id=ctx.turn_id, op="pref", table="kv", row_id=0, before={"key": key, "value": old},
                      label=key))


def has_changes(turn_id: str) -> bool:
    with session() as s:
        return s.query(Journal).filter(Journal.turn_id == turn_id, Journal.undone.is_(False)).count() > 0


def undo(turn_id: str) -> list[str]:
    """Revert every change of a turn, newest first. Returns human labels of what was reverted."""
    models = _models()
    reverted: list[str] = []
    with session() as s:
        rows = (
            s.query(Journal)
            .filter(Journal.turn_id == turn_id, Journal.undone.is_(False))
            .order_by(Journal.id.desc())
            .all()
        )
        for j in rows:
            try:
                if j.op == "pref":
                    prefs.set(j.before["key"], j.before["value"], s=s)
                else:
                    model = models[j.table]
                    if j.op == "create":
                        obj = s.get(model, j.row_id)
                        if obj is not None:
                            s.delete(obj)
                    elif j.op == "update":
                        obj = s.get(model, j.row_id)
                        if obj is not None:
                            for k, v in (j.before or {}).items():
                                setattr(obj, k, _load(v))
                    elif j.op == "delete":
                        if s.get(model, j.row_id) is None:
                            s.add(model(**{k: _load(v) for k, v in (j.before or {}).items()}))
                s.flush()
                j.undone = True
                if j.label:
                    reverted.append(j.label)
            except Exception:  # noqa: BLE001
                log.exception("undo of journal %s failed", j.id)
    return reverted


# ---------------------------------------------------------------- confirmation guard
def confirm_guard(ctx: Any, key: str, what: str, confirmed: bool) -> dict[str, Any] | None:
    """None = go ahead. Otherwise a tool result asking the model to get the user's confirmation."""
    if getattr(ctx, "trusted", False):  # explicit click in the web UI (it shows its own dialog)
        return None
    pending: dict[str, str] = dict(prefs.get("pending_confirmations") or {})
    turn = _turn(ctx) or ""
    if confirmed and key in pending and pending[key] != turn:
        pending.pop(key)
        prefs.set("pending_confirmations", pending)
        return None
    pending[key] = turn
    if len(pending) > 50:
        pending = dict(list(pending.items())[-50:])
    prefs.set("pending_confirmations", pending)
    return {
        "needs_confirmation": True,
        "what": what,
        "instruction": "Do NOT delete yet. Ask the user to confirm (one short question). Only after they confirm in "
        "their next message, call this tool again with confirmed=true.",
    }
