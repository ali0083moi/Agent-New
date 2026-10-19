from datetime import date, datetime, time

import pytest

from app import cal, prefs, timeutil
from app.db import RecurringEvent, session


def test_parse_jalali_and_gregorian():
    assert timeutil.parse_date("1405-07-27") == date(2026, 10, 19)
    assert timeutil.parse_date("۱۴۰۵/۰۸/۰۱") == date(2026, 10, 23)
    assert timeutil.parse_date("2026-10-19") == date(2026, 10, 19)
    assert timeutil.parse_datetime("1405-07-28 18:30") == datetime(2026, 10, 20, 18, 30)
    assert timeutil.parse_datetime("2026-10-20T08:00") == datetime(2026, 10, 20, 8, 0)
    # date only -> end of day by default
    assert timeutil.parse_datetime("2026-10-20") == datetime(2026, 10, 20, 23, 59)
    # timezone-aware input gets converted to local (Tehran = UTC+3:30)
    assert timeutil.parse_datetime("2026-10-20T10:00:00Z") == datetime(2026, 10, 20, 13, 30)


def test_weekdays_and_digits():
    assert timeutil.normalize_weekday("سه شنبه") == "tue"
    assert timeutil.normalize_weekday("سه‌شنبه") == "tue"
    assert timeutil.normalize_weekday("Thursday") == "thu"
    assert timeutil.normalize_weekday("پنج‌شنبه") == "thu"
    with pytest.raises(ValueError):
        timeutil.normalize_weekday("blah")
    assert timeutil.parse_time("۱۸:۳۰") == time(18, 30)
    assert timeutil.parse_time("8") == time(8, 0)
    assert timeutil.fa_digits("12:05") == "۱۲:۰۵"
    assert timeutil.fa_duration(150) == "۲ ساعت و ۳۰ دقیقه"


def test_week_start_is_saturday():
    assert timeutil.week_start(date(2026, 10, 19)) == date(2026, 10, 17)  # Monday -> Saturday
    assert timeutil.week_start(date(2026, 10, 17)) == date(2026, 10, 17)
    assert timeutil.week_start(date(2026, 10, 23)) == date(2026, 10, 17)  # Friday


def test_fa_date():
    assert timeutil.fa_date(date(2026, 10, 19)) == "دوشنبه ۲۷ مهر"


def _rec(**kw):
    base = dict(title="زبان", kind="language_class", weekdays=["tue", "thu"], start_time="18:00", end_time="20:00")
    base.update(kw)
    with session() as s:
        r = RecurringEvent(**base)
        s.add(r)
        s.flush()
        s.expunge(r)
        return r


def test_class_occurrences_and_next():
    r = _rec()
    with session() as s:
        items = cal.class_occurrences(s, datetime(2026, 10, 17), datetime(2026, 10, 24))
        assert [i.start for i in items] == [datetime(2026, 10, 20, 18), datetime(2026, 10, 22, 18)]
        rr = s.get(RecurringEvent, r.id)
        nxt = cal.next_occurrence(s, rr, datetime(2026, 10, 20, 19))
        assert nxt.start == datetime(2026, 10, 22, 18)


def test_week_parity():
    r = _rec(title="آمار", weekdays=["mon"], start_time="10:00", end_time="12:00", week_parity="odd")
    prefs.set("semester_start", "2026-10-17")  # this week = week 1 (odd)
    with session() as s:
        rr = s.get(RecurringEvent, r.id)
        assert cal.occurs_on(rr, date(2026, 10, 19), "2026-10-17")
        assert not cal.occurs_on(rr, date(2026, 10, 26), "2026-10-17")
        assert cal.occurs_on(rr, date(2026, 11, 2), "2026-10-17")
        items = cal.class_occurrences(s, datetime(2026, 10, 17), datetime(2026, 11, 7))
        assert len(items) == 2


def test_agenda_shape():
    _rec()
    with session() as s:
        days = cal.agenda(s, date(2026, 10, 20), 1)
    assert days[0]["weekday"] == "tue"
    assert days[0]["items"][0]["title"] == "زبان"
    assert days[0]["jalali"] == "1405/07/28"
