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
        nat = _natural(v)
        if nat is None:
            raise ValueError(f"تاریخ نامعتبر: {value} (از قالب YYYY-MM-DD استفاده کن)")
        return nat[0]
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 1700:
        return _jalali_to_gregorian(y, mo, d)
    return date(y, mo, d)


_GREG_MONTHS = {
    "january": 1, "jan": 1, "ژانویه": 1, "february": 2, "feb": 2, "فوریه": 2, "march": 3, "mar": 3, "مارس": 3,
    "april": 4, "apr": 4, "آوریل": 4, "may": 5, "مه": 5, "می": 5, "june": 6, "jun": 6, "ژوئن": 6,
    "july": 7, "jul": 7, "ژوئیه": 7, "جولای": 7, "august": 8, "aug": 8, "اوت": 8, "آگوست": 8,
    "september": 9, "sep": 9, "sept": 9, "سپتامبر": 9, "october": 10, "oct": 10, "اکتبر": 10,
    "november": 11, "nov": 11, "نوامبر": 11, "december": 12, "dec": 12, "دسامبر": 12,
}
_JAL_MONTHS = {name: i + 1 for i, name in enumerate(JALALI_MONTHS)} | {"اردی بهشت": 2, "امرداد": 5}
_DAY_WORDS = {
    "امروز": 0, "today": 0, "امشب": 0, "tonight": 0, "فردا": 1, "tomorrow": 1, "فرداشب": 1, "فردا شب": 1,
    "پس فردا": 2, "پسفردا": 2, "day after tomorrow": 2, "دیروز": -1, "yesterday": -1,
}


def _natural(text: str) -> tuple[date, time | None] | None:
    """Understand loose dates the model or user may write: 'امشب', 'فردا ساعت ۱۰', '۵ نوامبر',
    'سه شنبه ۱۸:۰۰', '۵ آبان ۱۴۰۵ ساعت ۹ صبح', 'Nov 5'."""
    v = re.sub(r"\s+", " ", to_latin_digits(text).lower().replace("\u200c", " ")).strip()
    v = re.sub(r"^(تا|قبل از|قبل|before|by|until|on|روز)\s+", "", v)
    now_ = now()
    d: date | None = None
    rest = v
    for word in sorted(_DAY_WORDS, key=len, reverse=True):
        if re.search(rf"(^|\s){re.escape(word)}(\s|$)", rest):
            d = now_.date() + timedelta(days=_DAY_WORDS[word])
            rest = rest.replace(word, " ")
            break
    if d is None:
        months = "|".join(sorted(map(re.escape, list(_GREG_MONTHS) + list(_JAL_MONTHS)), key=len, reverse=True))
        m = re.search(rf"(\d{{1,2}})\s*(?:ام|th|st|nd|rd)?\s+({months})(?:\s+(\d{{4}}))?", rest) or None
        m2 = None if m else re.search(rf"({months})\s+(\d{{1,2}})(?:,?\s+(\d{{4}}))?", rest)
        if m or m2:
            if m:
                day, mon, year = int(m.group(1)), m.group(2), m.group(3)
            else:
                mon, day, year = m2.group(1), int(m2.group(2)), m2.group(3)
            if mon in _JAL_MONTHS:
                jm = _JAL_MONTHS[mon]
                jy = int(year) if year else jalali(now_.date()).year
                d = _jalali_to_gregorian(jy, jm, day)
                if not year and d < now_.date():
                    d = _jalali_to_gregorian(jy + 1, jm, day)
            else:
                gm = _GREG_MONTHS[mon]
                gy = int(year) if year else now_.year
                d = date(gy, gm, day)
                if not year and d < now_.date():
                    d = date(gy + 1, gm, day)
            rest = rest.replace((m or m2).group(0), " ")
    if d is None:
        for alias, key in sorted(_WEEKDAY_ALIASES.items(), key=lambda x: -len(x[0])):
            if re.search(rf"(^|\s){re.escape(alias)}(\s|$)", rest):
                ahead = (WEEKDAY_KEYS.index(key) - now_.weekday()) % 7
                d = now_.date() + timedelta(days=ahead)
                rest = rest.replace(alias, " ")
                break
    if d is None:
        return None
    t: time | None = None
    tm = re.search(r"(?:ساعت\s*)(\d{1,2})(?::(\d{2}))?|(\d{1,2}):(\d{2})|(\d{1,2})\s*(?=(?:صبح|ظهر|عصر|بعد از ظهر|بعدازظهر|شب|am|pm))", rest)
    if tm:
        h = int(tm.group(1) or tm.group(3) or tm.group(5))
        mi = int(tm.group(2) or tm.group(4) or 0)
        morning = re.search(r"(صبح|بامداد|(?<![a-z])am\b)", v)
        evening = re.search(r"(عصر|شب|بعد ?از ?ظهر|(?<![a-z])pm\b)", v)
        noon = re.search(r"ظهر", v) and not evening
        if not morning and h < 12 and (evening or (noon and h < 6)):
            h += 12
        if 0 <= h <= 24 and 0 <= mi < 60:
            t = time(23, 59) if h == 24 else time(h, mi)
    elif re.search(r"(^|\s)(امشب|tonight|آخر شب|اخر شب|آخر امشب)", v):
        t = time(23, 59)
    if t is not None and re.search(r"(^|\s)(امروز|today)(\s|$)", v) is None and d == now_.date() and t <= now_.time() \
            and not re.search(r"(امشب|tonight)", v) and any(a in v for a in _WEEKDAY_ALIASES):
        d += timedelta(days=7)  # "Tuesday 9:00" said on Tuesday at noon = next week
    return d, t


def parse_datetime(value: str | datetime, default_time: time | None = None) -> datetime:
    """Parse a datetime given as ISO/Gregorian or Jalali, with optional time part.

    Also accepts loose natural forms (see `_natural`) so a slightly-off model argument still works."""
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
    m = re.match(r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2})(?:[T\s]+(\d{1,2}(?::\d{1,2}){0,2})(?:\.\d+)?)?$", v)
    if not m:
        nat = _natural(v)
        if nat is None:
            raise ValueError(f"تاریخ/ساعت نامعتبر: {value} (از قالب YYYY-MM-DD HH:MM استفاده کن)")
        d, t = nat
        return datetime.combine(d, t or default_time or time(23, 59))
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
