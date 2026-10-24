"""Google Calendar (iCal), Gmail (IMAP), university applications and strong-model routing."""

import asyncio
from datetime import datetime, timedelta
from email.message import EmailMessage

import httpx
from conftest import call
from fastapi.testclient import TestClient

from app import calsync, followups, mail, prefs
from app.agent import core, proactive, tools
from app.db import Application, ApplicationItem, Contact, Event, Followup, Message, RecurringEvent, Task, session
from app.llm import ChatResult


def run(c):
    return asyncio.run(c)


def ex(_tool, **args):
    ctx = tools.Ctx(channel="web", turn_id="t-test")
    res = run(tools.execute(_tool, args, ctx))
    tools.finalize(ctx)
    return res


# ---------------------------------------------------------------- Google Calendar
ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
BEGIN:VEVENT
UID:standup@google.com
DTSTART:20261019T053000Z
DTEND:20261019T060000Z
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
SUMMARY:Daily standup
LOCATION:شرکت
END:VEVENT
BEGIN:VEVENT
UID:holiday@google.com
DTSTART;VALUE=DATE:20261021
DTEND;VALUE=DATE:20261022
SUMMARY:تعطیل
END:VEVENT
BEGIN:VEVENT
UID:cancelled@google.com
DTSTART:20261020T100000Z
DTEND:20261020T110000Z
STATUS:CANCELLED
SUMMARY:Cancelled
END:VEVENT
BEGIN:VEVENT
UID:free@google.com
DTSTART:20261020T120000Z
DTEND:20261020T130000Z
TRANSP:TRANSPARENT
SUMMARY:Focus (free)
END:VEVENT
END:VCALENDAR
"""


def _mock_http(monkeypatch, module, handler):
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


def test_parse_feed_expands_recurring_events_in_local_time(clock):
    items = calsync.parse_feed(ICS, datetime(2026, 10, 19), datetime(2026, 10, 29))
    standups = [i for i in items if i["title"] == "Daily standup"]
    # Mon 19, Wed 21, Mon 26, Wed 28 — 05:30 UTC = 09:00 Tehran
    assert [i["start"] for i in standups] == [datetime(2026, 10, d, 9, 0) for d in (19, 21, 26, 28)]
    assert standups[0]["location"] == "شرکت" and standups[0]["busy"]
    titles = {i["title"] for i in items}
    assert "تعطیل" not in titles and "Cancelled" not in titles
    assert any(i["title"] == "Focus (free)" and not i["busy"] for i in items)


def test_sync_imports_updates_and_removes(clock, monkeypatch):
    feed = {"ics": ICS}
    _mock_http(monkeypatch, calsync, lambda req: httpx.Response(200, text=feed["ics"]))
    prefs.set("gcal_ics_urls", ["https://calendar.google.com/calendar/ical/x/private-abc/basic.ics"])
    res = run(calsync.sync())
    assert res["ok"] and res["added"] >= 6  # standups over the next 45 days, the "free" one skipped
    with session() as s:
        n = s.query(Event).filter(Event.source == "gcal").count()
        assert s.query(Event).filter(Event.title == "Focus (free)").count() == 0
    feed["ics"] = ICS.replace("RRULE:FREQ=WEEKLY;BYDAY=MO,WE", "RRULE:FREQ=WEEKLY;BYDAY=MO")
    res = run(calsync.sync())
    with session() as s:
        assert res["removed"] > 0 and s.query(Event).filter(Event.source == "gcal").count() < n
    assert prefs.get("gcal_last_sync")["ok"]


def test_imported_events_are_read_only(clock, monkeypatch):
    _mock_http(monkeypatch, calsync, lambda req: httpx.Response(200, text=ICS))
    prefs.set("gcal_ics_urls", ["https://example.com/basic.ics"])
    run(calsync.sync())
    with session() as s:
        eid = s.query(Event).filter(Event.source == "gcal").first().id
    assert "Google Calendar" in ex("update_event", event_id=eid, title="x")["error"]
    assert "Google Calendar" in ex("delete_event", event_id=eid)["error"]


def test_export_feed_for_google_calendar(clock, fake_llm):
    from app.main import create_app

    with session() as s:
        s.add(RecurringEvent(title="ریاضی", kind="university_class", weekdays=["tue"], start_time="10:00",
                             end_time="12:00", location="دانشگاه"))
        s.add(Event(title="از گوگل", kind="meeting", start_at=datetime(2026, 10, 20, 15), end_at=datetime(2026, 10, 20, 16),
                    source="gcal", external_id="x"))
        s.add(Task(title="گزارش", due_at=datetime(2026, 10, 21, 18)))
    ex("set_place", name="خانه", kind="home")
    ex("set_place", name="دانشگاه", kind="university")
    ex("set_travel_time", from_place="خانه", to_place="دانشگاه", minutes=45, mode="metro")
    client = TestClient(create_app())
    path = client.get("/api/calendar").json()["export_path"]
    r = client.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    body = r.text
    assert "🎓 ریاضی" in body and "ددلاین: گزارش" in body and "مسیر به دانشگاه" in body and "حاضر شدن" in body
    assert "از گوگل" not in body  # Google's own events aren't sent back
    assert client.get("/cal/wrong-token.ics").status_code == 404
    new_path = client.post("/api/calendar/token").json()["export_path"]
    assert new_path != path and client.get(path).status_code == 404


# ---------------------------------------------------------------- Gmail
def _mail(frm, to, subject, body, date="Mon, 19 Oct 2026 09:00:00 +0330"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = frm, to, subject, date
    m.set_content(body)
    return m.as_bytes()


class FakeIMAP:
    def __init__(self, boxes):
        self.boxes = boxes
        self.appended = []
        self.cur = None

    def login(self, user, password):
        if password != "abcdabcdabcdabcd":
            import imaplib

            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
                      b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Drafts"']

    def select(self, name, readonly=False):
        self.cur = name.strip('"')
        return "OK", [b"1"]

    def response(self, code):
        return code, [b"7"]

    def uid(self, cmd, *args):
        box = self.boxes.get(self.cur, [])
        if cmd == "search":
            crit = args[-1]
            lo = int(crit.split()[1].split(":")[0]) if crit.startswith("UID") else 0
            return "OK", [" ".join(str(u) for u, _ in box if u >= lo).encode()]
        uids = {int(x) for x in args[0].split(",")}
        return "OK", [(f"{u} (UID {u} BODY[] {{{len(m)}}}".encode(), m) for u, m in box if u in uids]

    def append(self, box, flags, date, msg):
        self.appended.append((box, flags, msg))
        return "OK", [b"APPEND completed"]

    def logout(self):
        pass


def _gmail(monkeypatch, boxes):
    fake = FakeIMAP(boxes)
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", lambda host, timeout=30: fake)
    prefs.set("gmail_address", "me@gmail.com")
    prefs.set("gmail_app_password", "abcd abcd abcd abcd")
    return fake


def test_gmail_first_check_catches_up_silently_then_reports_replies(clock, monkeypatch, fake_llm):
    ex("add_contact", name="Prof. Jane Smith", organization="U of T", email="Jane.Smith@utoronto.ca")
    ex("add_contact", name="Dr. Bob", organization="TUM", email="bob@tum.de")
    boxes = {"INBOX": [], "[Gmail]/Sent Mail": [(10, _mail("me@gmail.com", "Bob <bob@tum.de>", "PhD position", "Dear Bob"))]}
    _gmail(monkeypatch, boxes)
    res = run(mail.check())
    assert res["ok"] and res["first_run"] and res["sent"] == 1
    with session() as s:
        bob = s.query(Contact).filter(Contact.name == "Dr. Bob").one()
        assert bob.status == "contacted" and bob.next_followup_at is not None
        assert s.query(Followup).filter(Followup.kind == "contact_reply").count() == 0
    # a reply arrives
    boxes["INBOX"].append((5, _mail("Jane Smith <jane.smith@utoronto.ca>", "me@gmail.com", "Re: MSc supervision",
                                    "Hi, thanks for your email. Please send me your CV.\n\nOn Mon, me wrote:\n> old text")))
    res = run(mail.check())
    assert res["replies"] == 1 and not res["first_run"]
    with session() as s:
        jane = s.query(Contact).filter(Contact.name == "Prof. Jane Smith").one()
        f = s.query(Followup).filter(Followup.kind == "contact_reply").one()
        assert jane.status == "replied" and "Please send me your CV" in f.payload["text"] and "old text" not in f.payload["text"]
    fake_llm.script = [ChatResult(content="📩 استاد اسمیت جواب داد و رزومه‌ات رو خواسته.")]
    run(proactive.tick())
    with session() as s:
        assert s.query(Message).filter(Message.kind == "contact_reply").count() == 1
    # the same mail isn't reported twice
    assert run(mail.check())["replies"] == 0


def test_email_draft_goes_to_gmail_drafts(monkeypatch):
    ex("add_contact", name="Dr. Bob", organization="TUM", email="bob@tum.de")
    fake = _gmail(monkeypatch, {"INBOX": []})
    r = ex("create_email_draft", to="Dr. Bob", subject="MSc supervision", body="Dear Dr. Bob,\n\nI am ...")
    assert r["to"] == "bob@tum.de" and r["saved_to"] == '"[Gmail]/Drafts"'
    box, flags, raw = fake.appended[0]
    assert flags == "(\\Draft)" and b"bob@tum.de" in raw and b"MSc supervision" in raw


def test_gmail_not_connected_and_bad_password(monkeypatch):
    assert "not connected" in ex("create_email_draft", to="x@y.z", subject="s", body="b")["error"]
    _gmail(monkeypatch, {})
    prefs.set("gmail_app_password", "wrong")
    res = run(mail.check())
    assert not res["ok"] and "App Password" in res["error"]


def test_mail_settings_api_tests_the_login(monkeypatch):
    from app.main import create_app

    client = TestClient(create_app())
    _gmail(monkeypatch, {"INBOX": []})
    prefs.set("gmail_address", None)
    prefs.set("gmail_app_password", None)
    assert client.put("/api/mail", json={"address": "me@gmail.com", "app_password": "nope"}).status_code == 400
    r = client.put("/api/mail", json={"address": "me@gmail.com", "app_password": "abcd abcd abcd abcd"}).json()
    assert r["connected"] and r["address"] == "me@gmail.com"
    assert "app_password" not in str(client.get("/api/settings").json()["prefs"])


# ---------------------------------------------------------------- applications
def test_application_checklist_and_reminders(clock, fake_llm):
    ex("add_contact", name="Prof. Jane Smith", organization="University of Toronto", email="jane@utoronto.ca")
    r = ex("add_application", university="University of Toronto", program="MSc Computer Science", country="Canada",
           deadline="2026-10-26", recommendations=3, contact="jane smith")
    a = r["application"]
    assert a["total"] == 8 and a["contact"]["name"] == "Prof. Jane Smith" and a["deadline"] == "2026-10-26 23:59"
    ex("add_application", university="TU Munich", program="MSc Informatics", deadline="2026-12-01")
    r = ex("update_application_item", application="toronto", item="cv", status="done", all_applications=True)
    assert len(r["updated"]) == 2
    r = ex("update_application_item", application="Toronto", item="recommendation", status="requested", person="Dr. Karimi")
    assert r["updated"][0]["person"] == "Dr. Karimi"
    assert "already exists" in ex("add_application", university="University of Toronto", program="MSc Computer Science")["error"]
    # 7 days before the deadline: a reminder that lists what's missing
    with session() as s:
        followups.generate(s)
        f = s.query(Followup).filter(Followup.kind == "application_deadline").one()
        assert f.payload["days"] == 7
    fake_llm.script = [ChatResult(content="یک هفته تا ددلاین تورنتو…")]
    run(proactive.tick())
    trigger = [c for c in fake_llm.calls if "University application deadline" in c["messages"][-1]["content"]]
    assert trigger and "انگیزه‌نامه (SOP)" in trigger[0]["messages"][-1]["content"]
    r = ex("update_application", application="Toronto", status="submitted")
    assert r["application"]["submitted_at"] is not None
    with session() as s:
        followups.generate(s)
        assert s.query(Followup).filter(Followup.kind == "application_deadline", Followup.status == "pending").count() == 0


def test_applications_api(fake_llm):
    from app.main import create_app

    client = TestClient(create_app())
    r = client.post("/api/applications", json={"university": "ETH Zurich", "deadline": "2026-12-15"}).json()
    aid = r["application"]["id"]
    item = r["application"]["items"][0]
    assert client.patch(f"/api/applications/{aid}/items/{item['id']}", json={"status": "done"}).json()["status"] == "done"
    assert client.get("/api/applications").json()["applications"][0]["done"] == 1
    assert client.delete(f"/api/applications/{aid}").json()["ok"]
    with session() as s:
        assert s.query(ApplicationItem).count() == 0 and s.query(Application).count() == 0


# ---------------------------------------------------------------- strong model routing
def test_hard_jobs_use_the_strong_model(fake_llm):
    fake_llm.script = [ChatResult(content="Dear Prof. Smith, …")]
    run(core.handle_user_message("یه ایمیل برای استاد اسمیت بنویس درباره ریسرچش", "web"))
    assert fake_llm.calls[-1]["model"] == "fake-strong"
    fake_llm.script = [ChatResult(content="سلام!")]
    run(core.handle_user_message("سلام خوبی؟", "web"))
    assert fake_llm.calls[-1]["model"] is None  # the normal (cheap) chat model
    fake_llm.script = [call("use_strong_model", reason="complex trade-off"), ChatResult(content="…")]
    n = len(fake_llm.calls)
    run(core.handle_user_message("بین این دو تا موقعیت کاری کدوم بهتره؟", "web"))
    assert [c["model"] for c in fake_llm.calls[n:]] == [None, "fake-strong"]


def test_explicit_model_is_not_overridden(fake_llm):
    fake_llm.script = [ChatResult(content="…")]
    run(core.handle_user_message("انگیزه‌نامه منو بنویس", "web", model="model-under-test"))
    assert fake_llm.calls[-1]["model"] == "model-under-test"
