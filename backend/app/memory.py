"""Long-term memory: semantic recall over old messages, rolling summaries and automatic facts."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import numpy as np
from sqlalchemy import func

from . import prefs, timeutil
from .config import get_settings
from .db import ChatSession, Embedding, Memory, Message, Summary, session
from .llm import LLMError, get_llm

log = logging.getLogger(__name__)

HISTORY_WINDOW = 20  # messages of the current session sent verbatim to the model (see agent.core)
SUMMARY_CHUNK = 20  # older messages of a long session are summarized in chunks of this size
MIN_SCORE = 0.30

_cache: dict[str, Any] = {"max_id": 0, "keys": [], "matrix": None, "model": None}


# ---------------------------------------------------------------- vectors
def _to_blob(vec: list[float]) -> bytes:
    v = np.asarray(vec, dtype=np.float32)
    n = np.linalg.norm(v)
    return (v / n if n else v).tobytes()


def _load_index() -> tuple[list[tuple[str, int]], np.ndarray | None]:
    """Incrementally cached, L2-normalised matrix of all stored embeddings."""
    model = get_settings().embedding_model
    with session() as s:
        if _cache["model"] != model:
            _cache.update(max_id=0, keys=[], matrix=None, model=model)
        rows = (
            s.query(Embedding.id, Embedding.source_type, Embedding.source_id, Embedding.vector)
            .filter(Embedding.id > _cache["max_id"], Embedding.model == model)
            .order_by(Embedding.id)
            .all()
        )
    if rows:
        dim = _cache["matrix"].shape[1] if _cache["matrix"] is not None else len(rows[0].vector) // 4
        pairs = [((r.source_type, r.source_id), np.frombuffer(r.vector, dtype=np.float32)) for r in rows]
        pairs = [(k, v) for k, v in pairs if v.shape[0] == dim]
        if pairs:
            new = np.vstack([v for _, v in pairs])
            _cache["matrix"] = new if _cache["matrix"] is None else np.vstack([_cache["matrix"], new])
            _cache["keys"] = _cache["keys"] + [k for k, _ in pairs]
        _cache["max_id"] = rows[-1].id
    return _cache["keys"], _cache["matrix"]


def reset_cache() -> None:
    _cache.update(max_id=0, keys=[], matrix=None, model=None)


async def index_pending(limit: int = 64) -> int:
    """Embed messages/memories/summaries that have no vector yet."""
    if not prefs.get("semantic_memory", True):
        return 0
    model = get_settings().embedding_model
    with session() as s:
        done = {(e.source_type, e.source_id) for e in s.query(Embedding.source_type, Embedding.source_id).filter(Embedding.model == model)}
        todo: list[tuple[str, int, str]] = []
        for m in s.query(Message).order_by(Message.id.desc()).limit(2000):
            if ("message", m.id) not in done and len((m.content or "").strip()) >= 12:
                prefix = "کاربر: " if m.role == "user" else "دستیار: "
                todo.append(("message", m.id, prefix + m.content[:2000]))
        for mem in s.query(Memory).all():
            if ("memory", mem.id) not in done:
                todo.append(("memory", mem.id, mem.content))
        for sm in s.query(Summary).all():
            if ("summary", sm.id) not in done:
                todo.append(("summary", sm.id, sm.content[:4000]))
    todo = todo[:limit]
    if not todo:
        return 0
    try:
        vectors = await get_llm().embed([t[2] for t in todo])
    except LLMError as e:
        log.info("embedding skipped: %s", e)
        return 0
    with session() as s:
        for (stype, sid, txt), vec in zip(todo, vectors):
            s.add(Embedding(source_type=stype, source_id=sid, text=txt, vector=_to_blob(vec), model=model))
    return len(todo)


# ---------------------------------------------------------------- recall
_WORD = re.compile(r"[\w؀-ۿ]{3,}")
_STOP = set("این اون برای که با از به در تا را رو هم یه یک من تو کنم کن شد بود است هست میشه چی چه the and for you".split())


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(timeutil.to_latin_digits(text.lower())) if w not in _STOP}


def _source_text(stype: str, sid: int) -> tuple[str, str] | None:
    with session() as s:
        if stype == "message":
            m = s.get(Message, sid)
            return (m.content, timeutil.iso(m.created_at)) if m else None
        if stype == "memory":
            m = s.get(Memory, sid)
            return (m.content, timeutil.iso(m.created_at)) if m else None
        if stype == "summary":
            m = s.get(Summary, sid)
            return (m.content, timeutil.iso(m.created_at)) if m else None
    return None


def _current_session_id(s) -> int:
    from . import sessions

    return sessions.current_id(s)


def _recent_ids(window: int) -> set[int]:
    """The messages already in the model's verbatim history (no need to recall them)."""
    with session() as s:
        sid = _current_session_id(s)
        return {r[0] for r in s.query(Message.id).filter(Message.session_id == sid)
                .order_by(Message.id.desc()).limit(window + 1).all()}


async def recall(query: str, k: int = 6, window: int = HISTORY_WINDOW) -> list[dict[str, Any]]:
    """Find older context relevant to `query` (outside the verbatim history window)."""
    if not query.strip():
        return []
    recent = _recent_ids(window)
    hits: list[dict[str, Any]] = []
    keys, matrix = ([], None)
    if prefs.get("semantic_memory", True):
        keys, matrix = _load_index()
    if matrix is not None and len(keys):
        try:
            qv = np.frombuffer(_to_blob((await get_llm().embed([query[:2000]]))[0]), dtype=np.float32)
            if qv.shape[0] == matrix.shape[1]:
                scores = matrix @ qv
                for idx in np.argsort(-scores)[: k * 3]:
                    stype, sid = keys[int(idx)]
                    if scores[idx] < MIN_SCORE or (stype == "message" and sid in recent):
                        continue
                    src = _source_text(stype, sid)
                    if src:
                        hits.append({"type": stype, "id": sid, "text": src[0], "at": src[1], "score": float(scores[idx])})
                    if len(hits) >= k:
                        break
                return hits
        except LLMError as e:
            log.info("semantic recall unavailable, using keywords: %s", e)
    return _keyword_recall(query, k, recent)


def _keyword_recall(query: str, k: int, recent: set[int]) -> list[dict[str, Any]]:
    q = _tokens(query)
    if not q:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    with session() as s:
        for m in s.query(Message).order_by(Message.id.desc()).limit(3000):
            if m.id in recent:
                continue
            t = _tokens(m.content or "")
            overlap = len(q & t)
            if overlap:
                scored.append((overlap / (len(q) ** 0.5 * (len(t) ** 0.5 or 1)),
                               {"type": "message", "id": m.id, "text": m.content, "at": timeutil.iso(m.created_at)}))
        for sm in s.query(Summary).all():
            overlap = len(q & _tokens(sm.content))
            if overlap:
                scored.append((overlap / len(q) + 0.05,
                               {"type": "summary", "id": sm.id, "text": sm.content, "at": timeutil.iso(sm.created_at)}))
    scored.sort(key=lambda x: -x[0])
    return [dict(h, score=round(sc, 3)) for sc, h in scored[:k] if sc >= 0.15]


def format_recall(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return ""
    lines = ["## Possibly relevant older context (retrieved from long-term memory)"]
    for h in hits:
        text = h["text"].replace("\n", " ")
        if len(text) > 400:
            text = text[:400] + "…"
        lines.append(f"- ({h['type']}, {h['at']}) {text}")
    return "\n".join(lines)


# ---------------------------------------------------------------- summaries
SUMMARY_PROMPT = """Summarize this part of a conversation between a user and their personal assistant, in Persian,
as compact bullet points (max ~12). Keep: decisions, commitments, facts about the user, preferences, recurring
problems, emotional context, and anything the assistant promised to follow up on. Skip greetings and small talk.
Mention dates (Jalali) when relevant."""


def _transcript(rows: list[Message]) -> str:
    return "\n".join(
        f"[{timeutil.jalali_str(m.created_at)} {m.created_at.strftime('%H:%M')}] "
        f"{'کاربر' if m.role == 'user' else 'دستیار'}: {m.content[:800]}"
        for m in rows
    )


async def summarize_old(window: int = HISTORY_WINDOW) -> int:
    """Summarize messages of the current session that fell out of the verbatim history window, in chunks."""
    with session() as s:
        sid = _current_session_id(s)
        recent = s.query(Message.id).filter(Message.session_id == sid).order_by(Message.id.desc()).limit(window).all()
        if len(recent) < window:
            return 0
        cutoff = min(r[0] for r in recent)
        last_end = (s.query(func.coalesce(func.max(Summary.end_message_id), 0))
                    .filter(Summary.session_id == sid).scalar() or 0)
        rows = (
            s.query(Message)
            .filter(Message.session_id == sid, Message.id > last_end, Message.id < cutoff)
            .order_by(Message.id)
            .limit(SUMMARY_CHUNK)
            .all()
        )
        if len(rows) < SUMMARY_CHUNK // 2:
            return 0
        transcript = _transcript(rows)
        start_id, end_id = rows[0].id, rows[-1].id
    try:
        text = await get_llm().complete(SUMMARY_PROMPT, transcript, purpose="summary")
    except LLMError as e:
        log.info("summary skipped: %s", e)
        return 0
    with session() as s:
        s.add(Summary(session_id=sid, start_message_id=start_id, end_message_id=end_id, content=text.strip()))
    return 1


SESSION_SUMMARY_PROMPT = """This chat session between a user and their personal assistant has ended. Write a short
summary in Persian (max ~8 bullet points) of what matters for the next conversation: open questions, what the
assistant promised to do or check, decisions, the user's mood/situation, and anything left unfinished. Skip what was
simply saved (tasks, events) unless it needs follow-up. If nothing matters, answer with an empty string."""


async def summarize_session(session_id: int) -> bool:
    """Summary of a finished session, carried into the next one (see sessions.previous_summary)."""
    with session() as s:
        cs = s.get(ChatSession, session_id)
        if cs is None or cs.summary:
            return False
        rows = (s.query(Message).filter(Message.session_id == session_id).order_by(Message.id.desc()).limit(60).all())
        if sum(1 for m in rows if m.role == "user") < 2:
            return False
        earlier = [x.content for x in s.query(Summary).filter(Summary.session_id == session_id).order_by(Summary.id)]
        transcript = ("\n".join(earlier) + "\n\n" if earlier else "") + _transcript(list(reversed(rows)))
    try:
        text = await get_llm().complete(SESSION_SUMMARY_PROMPT, transcript, purpose="summary")
    except LLMError as e:
        log.info("session summary skipped: %s", e)
        return False
    with session() as s:
        cs = s.get(ChatSession, session_id)
        if cs is not None:
            cs.summary = text.strip() or "-"
    return True


def latest_summaries(n: int = 2) -> list[Summary]:
    with session() as s:
        sid = _current_session_id(s)
        rows = (s.query(Summary).filter(Summary.session_id == sid)
                .order_by(Summary.end_message_id.desc()).limit(n).all())
        for r in rows:
            s.expunge(r)
    return list(reversed(rows))


# ---------------------------------------------------------------- automatic facts
FACTS_PROMPT = """You maintain long-term memory about a user for their personal assistant.
From the latest exchange, extract only DURABLE facts about the user worth remembering for months: job/employer,
studies (university, major, courses), goals (e.g. IELTS target), routines, preferences, people in their life,
health/sleep habits, recurring difficulties. Do NOT store tasks, deadlines, appointments or one-off plans
(those are stored elsewhere), and do NOT store things the assistant said unless the user confirmed them.
Existing memories are given with ids; prefer updating an existing one over adding a near-duplicate, and delete
memories the user clearly contradicted.
Answer ONLY with JSON: {"add":[{"content":"...","category":"profile|preference|routine|person|work|study|fact"}],
"update":[{"id":1,"content":"..."}],"delete":[2]} — use empty lists when nothing is worth saving. Write contents in Persian."""


# short replies with nothing to learn from (no model call for them)
_ACK = re.compile(r"(باشه|اوکی|اوکیه|ok|okay|مرسی|ممنون|ممنونم|دمت گرم|آره|اره|نه|حله|چشم|انجام شد|شروع کردم|تموم شد|"
                  r"راه افتادم|بعدا|بعداً|نه مرسی)[\s!.؟?👍🙏✅]*", re.I)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", timeutil.to_latin_digits(t).strip().lower())


async def extract_facts(user_message_id: int) -> dict[str, int]:
    if not prefs.get("auto_facts", True):
        return {}
    with session() as s:
        um = s.get(Message, user_message_id)
        if um is None or len((um.content or "").strip()) < 8 or _ACK.fullmatch((um.content or "").strip()):
            return {}
        reply = s.query(Message).filter(Message.id > um.id, Message.role == "assistant").order_by(Message.id).first()
        mems = s.query(Memory).order_by(Memory.id).all()
        existing = [{"id": m.id, "content": m.content, "category": m.category} for m in mems]
        exchange = f"USER: {um.content}\nASSISTANT: {reply.content if reply else ''}"
    try:
        raw = await get_llm().complete(
            FACTS_PROMPT, f"Existing memories: {json.dumps(existing, ensure_ascii=False)}\n\nLatest exchange:\n{exchange}",
            purpose="facts",
        )
    except LLMError as e:
        log.info("fact extraction skipped: %s", e)
        return {}
    data = _parse_json(raw)
    if not isinstance(data, dict):
        return {}
    out = {"added": 0, "updated": 0, "deleted": 0}
    with session() as s:
        for item in data.get("update") or []:
            if not isinstance(item, dict):
                continue
            m = s.get(Memory, int(item.get("id") or 0))
            if m and item.get("content"):
                m.content = str(item["content"]).strip()
                s.query(Embedding).filter(Embedding.source_type == "memory", Embedding.source_id == m.id).delete()
                out["updated"] += 1
        s.flush()
        known = {_norm(m.content) for m in s.query(Memory).all()}
        for item in data.get("add") or []:
            content = str(item.get("content") or "").strip() if isinstance(item, dict) else ""
            if len(content) < 4 or _norm(content) in known or any(_norm(content) in k or k in _norm(content) for k in known):
                continue
            s.add(Memory(content=content, category=str(item.get("category") or "fact")[:32], source="auto"))
            known.add(_norm(content))
            out["added"] += 1
        for mid in data.get("delete") or []:
            try:
                m = s.get(Memory, int(mid))
            except (TypeError, ValueError):
                continue
            if m and m.source == "auto":
                s.delete(m)
                out["deleted"] += 1
    if any(out.values()):
        reset_cache()
    return out


def _parse_json(raw: str) -> Any:
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, flags=re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
