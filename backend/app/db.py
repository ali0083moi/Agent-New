"""Database engine, session and ORM models (SQLite by default)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import get_settings
from . import timeutil


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return timeutil.now()


class Message(Base):
    """One unified conversation shared by the web UI and Telegram."""

    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    channel: Mapped[str] = mapped_column(String(16), default="web")  # web | telegram | system
    kind: Mapped[str] = mapped_column(String(32), default="chat")  # chat | followup kind
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class Goal(Base):
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(32), default="personal")
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|achieved|dropped
    progress_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class RecurringEvent(Base):
    """Weekly recurring commitments: university classes, language classes, work shifts..."""

    __tablename__ = "recurring_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32), default="university_class")
    weekdays: Mapped[list[str]] = mapped_column(JSON, default=list)  # ["tue", "thu"]
    start_time: Mapped[str] = mapped_column(String(5))  # "18:00"
    end_time: Mapped[str] = mapped_column(String(5))
    location: Mapped[str] = mapped_column(String(200), default="")
    instructor: Mapped[str] = mapped_column(String(200), default="")
    week_parity: Mapped[str] = mapped_column(String(8), default="all")  # all|odd|even
    followup: Mapped[bool] = mapped_column(Boolean, default=True)  # ask about homework after
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id", ondelete="SET NULL"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Event(Base):
    """One-off calendar items: appointments, outings, exams..."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32), default="appointment")
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    location: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(32), default="personal")
    status: Mapped[str] = mapped_column(String(16), default="todo", index=True)  # todo|doing|done|cancelled
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 high, 2 normal, 3 low
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # percent 0..100
    notes: Mapped[str] = mapped_column(Text, default="")
    recurring_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurring_events.id", ondelete="SET NULL"), nullable=True
    )
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PlannedBlock(Base):
    """A time block the planner reserved for working on a task."""

    __tablename__ = "planned_blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="planned")  # planned|done|partial|skipped
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class WorkLog(Base):
    """Time actually spent. An open focus session has end_at = NULL."""

    __tablename__ = "work_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(32), default="personal")
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    productive: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(16), default="chat")  # focus|chat|manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Followup(Base):
    """Proactive messages the assistant will send (reminders, check-ins, reports)."""

    __tablename__ = "followups"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|sent|cancelled
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True)
    recurring_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurring_events.id", ondelete="CASCADE"), nullable=True
    )
    block_id: Mapped[int | None] = mapped_column(ForeignKey("planned_blocks.id", ondelete="CASCADE"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(16), default="system")  # system|agent
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Memory(Base):
    """Long-term facts the assistant knows about the user."""

    __tablename__ = "memories"
    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), default="fact")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)
    week_end: Mapped[date] = mapped_column(Date)
    work_report: Mapped[str] = mapped_column(Text, default="")
    personal_report: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class KV(Base):
    """User preferences and small bits of app state."""

    __tablename__ = "kv"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def init_engine(url: str | None = None):
    global _engine, _SessionLocal
    url = url or get_settings().database_url
    if url.startswith("sqlite:///"):
        from pathlib import Path

        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )
    if url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session() -> Iterator[Session]:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
