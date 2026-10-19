import asyncio

import pytest
from conftest import call
from fastapi.testclient import TestClient

from app import prefs, telegram
from app.db import Message, Task, session
from app.llm import ChatResult


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- formatting
def test_md_to_html_basic():
    out = telegram.md_to_html("**مهم:** کار <فوری>\n- اول\n- دوم\n# تیتر\n`code`")
    assert "<b>مهم:</b>" in out
    assert "&lt;فوری&gt;" in out
    assert "• اول" in out and "• دوم" in out
    assert "<b>تیتر</b>" in out
    assert "<code>code</code>" in out


def test_md_to_html_quote_and_table():
    out = telegram.md_to_html("> خط ۱\n> خط ۲\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<blockquote>خط ۱\nخط ۲</blockquote>" in out
    assert "a | b" in out and "1 | 2" in out and "---" not in out


def test_md_italic_does_not_break_snake_case():
    assert telegram.md_to_html("file_name_here") == "file_name_here"
    assert telegram.md_to_html("_از وب:_") == "<i>از وب:</i>"


def test_split_text():
    parts = telegram.split_text("a" * 5000 + "\n" + "b" * 10, limit=4000)
    assert all(len(p) <= 4000 for p in parts)
    assert "".join(parts).replace("\n", "") == "a" * 5000 + "b" * 10


# ---------------------------------------------------------------- bot flow
class FakeBot(telegram.TelegramBot):
    def __init__(self):
        super().__init__("TOKEN", "https://api.telegram.org")
        self.sent: list[dict] = []

    async def call(self, method, **params):
        self.sent.append({"method": method, **params})
        return {"message_id": len(self.sent)}


@pytest.fixture
def bot(monkeypatch):
    b = FakeBot()
    monkeypatch.setattr(telegram, "bot", b)
    return b


def test_pairing_then_chat(bot, fake_llm):
    code = telegram.pairing_code()
    chat = {"id": 42, "type": "private"}
    # wrong code -> refused
    run(bot.handle({"message": {"chat": chat, "from": {"first_name": "Ali"}, "text": "/start 000"}}))
    assert telegram.owner_chat_id() is None
    run(bot.handle({"message": {"chat": chat, "from": {"id": 7, "first_name": "Ali"}, "text": f"/start {code}"}}))
    assert str(telegram.owner_chat_id()) == "42"
    assert prefs.get("user_name") == "Ali"

    fake_llm.script = [call("add_task", title="تمرین ریاضی", due="2026-10-21 10:00"), ChatResult(content="✅ ثبت شد")]
    run(bot.handle({"message": {"chat": chat, "text": "تا چهارشنبه ده صبح باید تمرین ریاضی بدم"}}))
    # the reply streams into a placeholder message which is then edited into the final text
    assert [m["text"] for m in bot.sent if m["method"] == "sendMessage"][-1] == "✍️ …"
    final = [m for m in bot.sent if m["method"] == "editMessageText"][-1]
    assert final["text"] == "✅ ثبت شد" and final["parse_mode"] == "HTML"
    # the change can be undone with an inline button
    assert final["reply_markup"]["inline_keyboard"][-1][0]["text"] == "↩️ برگردون"
    with session() as s:
        assert s.query(Task).one().title == "تمرین ریاضی"
        chans = [m.channel for m in s.query(Message).order_by(Message.id)]
        assert chans == ["telegram", "telegram"]

    # a stranger is ignored
    run(bot.handle({"message": {"chat": {"id": 99, "type": "private"}, "text": "hi"}}))
    assert bot.sent[-1]["chat_id"] == 99 and "شخصی" in bot.sent[-1]["text"]


def test_quick_reply_callback(bot, fake_llm):
    prefs.set("telegram_chat_id", 42)
    with session() as s:
        m = Message(role="assistant", content="شروع کنیم؟", channel="system", kind="block_start",
                    meta={"quick_replies": ["شروع کردم ▶️", "بعداً"]})
        s.add(m)
        s.flush()
        mid = m.id
    fake_llm.script = [ChatResult(content="عالی!")]
    run(bot.handle({"callback_query": {"id": "cb1", "data": f"qr:{mid}:0", "from": {},
                                       "message": {"message_id": 5, "chat": {"id": 42}}}}))
    with session() as s:
        user_msgs = s.query(Message).filter(Message.role == "user").all()
        assert user_msgs[-1].content == "شروع کردم ▶️"


def test_proactive_message_reaches_telegram_with_buttons(bot, fake_llm):
    from app import notify

    prefs.set("telegram_chat_id", 42)
    data = run(notify.deliver("یادآوری!", "custom", meta={"quick_replies": ["باشه"]}))
    send = [m for m in bot.sent if m["method"] == "sendMessage"][-1]
    assert send["chat_id"] == 42
    assert send["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"qr:{data['id']}:0"


# ---------------------------------------------------------------- HTTP API
@pytest.fixture
def client(fake_llm):
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def test_api_chat_and_dashboard(client, fake_llm):
    fake_llm.script = [call("add_task", title="ارائه سمینار", due="1405-08-04 10:00", priority=1),
                       ChatResult(content="ثبت شد ✅")]
    r = client.post("/api/chat", data={"text": "شنبه ارائه سمینار دارم"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assistant"]["content"] == "ثبت شد ✅"
    assert body["assistant"]["meta"]["actions"][0]["label"].endswith("ارائه سمینار")

    msgs = client.get("/api/messages").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]

    dash = client.get("/api/dashboard").json()
    assert dash["open_count"] == 1
    assert dash["tasks"][0]["due"] == "2026-10-26 10:00"


def test_api_tasks_crud(client):
    r = client.post("/api/tasks", json={"title": "تست", "estimated_minutes": 30})
    tid = r.json()["task"]["id"]
    r = client.patch(f"/api/tasks/{tid}", json={"status": "done"})
    assert r.json()["task"]["status"] == "done"
    assert client.get("/api/tasks?status=open").json()["tasks"] == []
    assert client.delete(f"/api/tasks/{tid}").status_code == 200
    r = client.patch("/api/tasks/999", json={"title": "x"})
    assert r.status_code == 400


def test_api_recurring_and_agenda(client):
    r = client.post("/api/recurring", json={"title": "ریاضی ۲", "weekdays": ["sat", "mon"], "start_time": "8:00",
                                            "end_time": "10:00", "location": "کلاس ۲۰۴"})
    assert r.status_code == 200, r.text
    days = client.get("/api/agenda?start=2026-10-17&days=7").json()["days"]
    titles = [it["title"] for d in days for it in d["items"]]
    assert titles.count("ریاضی ۲") == 2


def test_api_settings(client):
    r = client.patch("/api/settings", json={"wake_time": "۷:۰۰", "weekly_report_day": "جمعه"})
    assert r.status_code == 200, r.text
    p = client.get("/api/settings").json()["prefs"]
    assert p["wake_time"] == "07:00" and p["weekly_report_day"] == "fri"
    assert client.patch("/api/settings", json={"nope": 1}).status_code == 400


def test_auth_required(monkeypatch, fake_llm):
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("APP_PASSWORD", "secret")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        assert c.get("/api/dashboard").status_code == 401
        me = c.get("/api/me").json()
        assert me["auth_required"] is True and me["authenticated"] is False and "telegram" not in me
        assert c.post("/api/login", json={"password": "bad"}).status_code == 401
        assert c.post("/api/login", json={"password": "secret"}).status_code == 200
        assert c.get("/api/dashboard").status_code == 200


def test_uploads_require_auth(monkeypatch, tmp_path, fake_llm):
    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("APP_PASSWORD", "secret")
    get_settings.cache_clear()
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "uploads" / "a.png").write_bytes(b"png")
    with TestClient(create_app()) as c:
        assert c.get("/uploads/a.png").status_code == 401
        c.post("/api/login", json={"password": "secret"})
        assert c.get("/uploads/a.png").content == b"png"
        # path traversal never reaches the database file (may fall through to the SPA index)
        assert b"SQLite" not in c.get("/uploads/..%2Ftest.db").content
        assert b"SQLite" not in c.get("/uploads/%2E%2E/test.db").content
