"""IELTS coach: essay & speaking grading against the public band descriptors, scores, vocabulary SRS."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from . import prefs, timeutil
from .db import IeltsScore, VocabWord
from .llm import LLMError, get_llm

SKILLS = ["listening", "reading", "writing", "speaking", "overall"]
SKILL_FA = {"listening": "لیسنینگ", "reading": "ریدینگ", "writing": "رایتینگ", "speaking": "اسپیکینگ", "overall": "کلی"}
LEITNER_DAYS = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16}

WRITING_RUBRIC = """You are a strict, experienced IELTS examiner. Grade the candidate's {task} using the official public
band descriptors. Criteria: {criteria}. Be realistic (do not inflate); half bands allowed. The overall writing band is
the average of the four criteria rounded down to the nearest half band.
Return ONLY JSON:
{{"criteria": {{"<criterion>": {{"band": 6.5, "comment": "..."}}}}, "overall": 6.5,
 "word_count": 270, "strengths": ["..."], "improvements": ["... (most important first)"],
 "corrections": [{{"original": "...", "better": "...", "why": "..."}}],
 "vocabulary": [{{"word": "...", "meaning_fa": "...", "example": "..."}}],
 "next_step": "one concrete exercise for next time"}}
Comments and improvements in Persian (Farsi); quotes/corrections in English. Max 6 corrections, max 6 vocabulary items."""

WRITING_CRITERIA = {
    "task1": "task_achievement, coherence_cohesion, lexical_resource, grammatical_range_accuracy",
    "task2": "task_response, coherence_cohesion, lexical_resource, grammatical_range_accuracy",
}

SPEAKING_RUBRIC = """You are an experienced IELTS speaking examiner. The candidate answered a Part {part} question.
You only have an automatic transcript (it may contain recognition errors; do not punish obvious ASR mistakes).
Criteria: fluency_coherence, lexical_resource, grammatical_range_accuracy, pronunciation (estimate cautiously from
the transcript only and say it is an estimate). Half bands allowed. Return ONLY JSON:
{{"criteria": {{"<criterion>": {{"band": 6.0, "comment": "..."}}}}, "overall": 6.0,
 "strengths": ["..."], "improvements": ["..."], "better_answer": "a band-8 style model answer (English, short)",
 "vocabulary": [{{"word": "...", "meaning_fa": "...", "example": "..."}}]}}
Comments in Persian (Farsi)."""


def _round_half_down(x: float) -> float:
    return int(x * 2) / 2


def _parse(raw: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", raw, flags=re.S)
    if not m:
        raise LLMError("پاسخ مدل قابل خواندن نبود")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON نامعتبر از مدل: {e}") from e


async def _grade(system: str, user: str) -> dict[str, Any]:
    llm = get_llm()
    res = await llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                         model=llm.model("ielts"), purpose="ielts")
    return _parse(res.content)


async def grade_writing(essay: str, task_type: str = "task2", question: str = "") -> dict[str, Any]:
    task_type = task_type if task_type in WRITING_CRITERIA else "task2"
    data = await _grade(
        WRITING_RUBRIC.format(task="Writing " + task_type.replace("task", "Task "), criteria=WRITING_CRITERIA[task_type]),
        f"Question: {question or '(not provided)'}\n\nEssay:\n{essay}",
    )
    bands = [float(v.get("band", 0)) for v in (data.get("criteria") or {}).values() if isinstance(v, dict)]
    if bands:
        data["overall"] = _round_half_down(sum(bands) / len(bands))
    data["word_count"] = len(re.findall(r"[A-Za-z']+", essay))
    return data


async def grade_speaking(transcript: str, question: str, part: int = 2) -> dict[str, Any]:
    data = await _grade(SPEAKING_RUBRIC.format(part=part), f"Question: {question}\n\nTranscript:\n{transcript}")
    bands = [float(v.get("band", 0)) for v in (data.get("criteria") or {}).values() if isinstance(v, dict)]
    if bands:
        data["overall"] = round(sum(bands) / len(bands) * 2) / 2  # speaking rounds to nearest half
    return data


def save_score(s: Session, skill: str, band: float, source: str, details: dict[str, Any] | None = None,
               day: date | None = None, note: str = "") -> IeltsScore:
    sc = IeltsScore(day=day or timeutil.today(), skill=skill, band=float(band), source=source,
                    details=details or {}, note=note)
    s.add(sc)
    s.flush()
    return sc


def progress(s: Session) -> dict[str, Any]:
    target = float(prefs.get("ielts_target_band", s=s) or 7.0)
    exam = prefs.get("ielts_exam_date", s=s)
    rows = s.query(IeltsScore).order_by(IeltsScore.day, IeltsScore.id).all()
    series: dict[str, list[dict[str, Any]]] = {k: [] for k in SKILLS}
    for r in rows:
        series.setdefault(r.skill, []).append(
            {"date": r.day.isoformat(), "band": r.band, "source": r.source, "id": r.id}
        )
    latest = {}
    for k, pts in series.items():
        if pts:
            recent = [p["band"] for p in pts[-3:]]
            latest[k] = {"last": pts[-1]["band"], "avg_recent": round(sum(recent) / len(recent), 2)}
    exam_d = timeutil.parse_date(exam) if exam else None
    today = timeutil.today()
    return {
        "target_band": target,
        "exam_date": exam_d.isoformat() if exam_d else None,
        "days_left": (exam_d - today).days if exam_d else None,
        "series": series,
        "latest": latest,
        "gaps": {k: round(target - v["avg_recent"], 2) for k, v in latest.items() if k != "overall"},
        "vocab": vocab_stats(s),
    }


# ---------------------------------------------------------------- vocabulary (Leitner)
def add_words(s: Session, words: list[dict[str, Any]]) -> list[VocabWord]:
    out = []
    existing = {w.word.lower() for w in s.query(VocabWord).all()}
    for item in words:
        word = str(item.get("word", "")).strip()
        if not word or word.lower() in existing:
            continue
        w = VocabWord(word=word, meaning=str(item.get("meaning") or item.get("meaning_fa") or ""),
                      example=str(item.get("example") or ""), box=1, due=timeutil.today())
        s.add(w)
        existing.add(word.lower())
        out.append(w)
    s.flush()
    return out


def due_words(s: Session, n: int = 8) -> list[VocabWord]:
    return (
        s.query(VocabWord)
        .filter(VocabWord.due <= timeutil.today())
        .order_by(VocabWord.box, VocabWord.due, VocabWord.id)
        .limit(n)
        .all()
    )


def review(s: Session, word: VocabWord, correct: bool) -> VocabWord:
    today = timeutil.today()
    word.reviews += 1
    if correct:
        word.correct += 1
        word.box = min(5, word.box + 1)
    else:
        word.box = 1
    word.due = today + timedelta(days=LEITNER_DAYS[word.box])
    word.last_reviewed = today
    s.flush()
    return word


def vocab_stats(s: Session) -> dict[str, Any]:
    words = s.query(VocabWord).all()
    return {
        "total": len(words),
        "due_today": sum(1 for w in words if w.due <= timeutil.today()),
        "mastered": sum(1 for w in words if w.box >= 5),
        "by_box": {b: sum(1 for w in words if w.box == b) for b in range(1, 6)},
    }


def word_dict(w: VocabWord) -> dict[str, Any]:
    return {"id": w.id, "word": w.word, "meaning": w.meaning, "example": w.example, "box": w.box,
            "due": w.due.isoformat(), "reviews": w.reviews, "correct": w.correct}
