"""Time helpers: local "now", Jalali (Persian) calendar, flexible parsing.

All datetimes inside the app are *naive local time* in the configured timezone
(single-user app, Asia/Tehran by default, which has no DST).
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import jdatetime

from .config import get_settings

WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # python weekday() order
WEEKDAY_FA = {
    "sat": "شنبه",
    "sun": "یکشنبه",
    "mon": "دوشنبه",
    "tue": "سه‌شنبه",
    "wed": "چهارشنبه",
    "thu": "پنجشنبه",
    "fri": "جمعه",
}
# Iranian week order (starts on Saturday)
WEEK_ORDER = ["sat", "sun", "mon", "tue", "wed", "thu", "fri"]

JALALI_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]

_FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_TO_LATIN = {ord(c): str(i) for i, c in enumerate(_FA_DIGITS)} | {
    ord(c): str(i) for i, c in enumerate(_AR_DIGITS)
}
_TO_FA = {ord(str(i)): c for i, c in enumerate(_FA_DIGITS)}

_WEEKDAY_ALIASES = {
    "sat": "sat", "saturday": "sat", "شنبه": "sat",
    "sun": "sun", "sunday": "sun", "یکشنبه": "sun", "یک‌شنبه": "sun", "یک شنبه": "sun",
    "mon": "mon", "monday": "mon", "دوشنبه": "mon", "دو‌شنبه": "mon", "دو شنبه": "mon",
    "tue": "tue", "tuesday": "tue", "سه‌شنبه": "tue", "سه شنبه": "tue", "سهشنبه": "tue",
    "wed": "wed", "wednesday": "wed", "چهارشنبه": "wed", "چهار‌شنبه": "wed", "چهار شنبه": "wed",
    "thu": "thu", "thursday": "thu", "پنجشنبه": "thu", "پنج‌شنبه": "thu", "پنج شنبه": "thu",
    "fri": "fri", "friday": "fri", "جمعه": "fri",
}


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def now() -> datetime:
    """Current local time (naive, minute precision is enough for us)."""
    return datetime.now(tz()).replace(tzinfo=None, microsecond=0)


def today() -> date:
    return now().date()


def to_latin_digits(s: str) -> str:
    return s.translate(_TO_LATIN)


def fa_digits(s: object) -> str:
    return str(s).translate(_TO_FA)


def weekday_key(d: date | datetime) -> str:
    return WEEKDAY_KEYS[d.weekday()]


def normalize_weekday(value: str) -> str:
    v = to_latin_digits(str(value)).strip().lower().replace("‌", "‌")
    if v in _WEEKDAY_ALIASES:
        return _WEEKDAY_ALIASES[v]
    v2 = v.replace("‌", "").replace(" ", "")
    for alias, key in _WEEKDAY_ALIASES.items():
        if alias.replace("‌", "").replace(" ", "") == v2:
            return key
    raise ValueError(f"روز هفته نامعتبر: {value}")


def parse_time(value: str) -> time:
    v = to_latin_digits(str(value)).strip().replace(".", ":")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?(?::\d{1,2})?", v)
    if not m:
        raise ValueError(f"ساعت نامعتبر: {value}")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if h == 24 and mi == 0:
        return time(23, 59)
    return time(h, mi)


def _jalali_to_gregorian(y: int, m: int, d: int) -> date:
    return jdatetime.date(y, m, d).togregorian()


def parse_date(value: str | date) -> date:
    """Parse 'YYYY-MM-DD' (Gregorian) or Jalali 'YYYY-MM-DD' / 'YYYY/MM/DD' (year < 1700)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    v = to_latin_digits(str(value)).strip()
    low = v.lower()
    if low in ("today", "امروز"):
        return today()
    if low in ("tomorrow", "فردا"):
        return today() + timedelta(days=1)
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", v)
    if not m:
        raise ValueError(f"تاریخ نامعتبر: {value}")
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 1700:
        return _jalali_to_gregorian(y, mo, d)
    return date(y, mo, d)


def parse_datetime(value: str | datetime, default_time: time | None = None) -> datetime:
    """Parse a datetime given as ISO/Gregorian or Jalali, with optional time part."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None, microsecond=0) if value.tzinfo is None else (
            value.astimezone(tz()).replace(tzinfo=None, microsecond=0)
        )
    v = to_latin_digits(str(value)).strip()
    # full ISO with timezone -> convert to local
    if re.search(r"[+-]\d{2}:?\d{2}$|Z$", v) and "T" in v:
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt.astimezone(tz()).replace(tzinfo=None, microsecond=0)
        except ValueError:
            pass
    m = re.match(r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2})(?:[T\s]+(\d{1,2}(?::\d{1,2}){0,2}))?$", v)
    if not m:
        raise ValueError(f"تاریخ/ساعت نامعتبر: {value}")
    d = parse_date(m.group(1))
    if m.group(2):
        t = parse_time(m.group(2))
    else:
        t = default_time or time(23, 59)
    return datetime.combine(d, t)


def jalali(d: date | datetime) -> jdatetime.date:
    if isinstance(d, datetime):
        d = d.date()
    return jdatetime.date.fromgregorian(date=d)


def jalali_str(d: date | datetime) -> str:
    j = jalali(d)
    return f"{j.year:04d}/{j.month:02d}/{j.day:02d}"


def fa_date(d: date | datetime, with_weekday: bool = True, with_year: bool = False) -> str:
    j = jalali(d)
    s = f"{j.day} {JALALI_MONTHS[j.month - 1]}"
    if with_year:
        s += f" {j.year}"
    if with_weekday:
        s = f"{WEEKDAY_FA[weekday_key(d)]} {s}"
    return fa_digits(s)


def fa_time(t: datetime | time) -> str:
    return fa_digits(f"{t.hour:02d}:{t.minute:02d}")


def fa_datetime(dt: datetime) -> str:
    return f"{fa_date(dt)} ساعت {fa_time(dt)}"


def fa_duration(minutes: int | float) -> str:
    minutes = int(round(minutes or 0))
    h, m = divmod(abs(minutes), 60)
    parts = []
    if h:
        parts.append(f"{h} ساعت")
    if m or not h:
        parts.append(f"{m} دقیقه")
    return fa_digits(" و ".join(parts))


def iso(dt: datetime | date | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M")
    return dt.isoformat()


def week_start(d: date) -> date:
    """Saturday on or before d (Iranian week)."""
    # python: Monday=0 ... Saturday=5, Sunday=6
    offset = (d.weekday() - 5) % 7
    return d - timedelta(days=offset)


def at(d: date, t: time | str) -> datetime:
    if isinstance(t, str):
        t = parse_time(t)
    return datetime.combine(d, t)


def calendar_table(start: date, days: int = 14) -> str:
    """Compact day table for the LLM: weekday, Jalali, Gregorian."""
    lines = []
    for i in range(days):
        d = start + timedelta(days=i)
        tag = " (today)" if i == 0 else (" (tomorrow)" if i == 1 else "")
        j = jalali(d)
        lines.append(
            f"- {WEEKDAY_FA[weekday_key(d)]} ({weekday_key(d)}) | جلالی {j.year}/{j.month:02d}/{j.day:02d}"
            f" {JALALI_MONTHS[j.month - 1]} | {d.isoformat()}{tag}"
        )
    return "\n".join(lines)


def in_window(t: time, start: time, end: time) -> bool:
    """Is clock-time t within [start, end) where the window may cross midnight."""
    if start <= end:
        return start <= t < end
    return t >= start or t < end
