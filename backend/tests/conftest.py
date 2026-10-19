import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("GAPGPT_API_KEY", "test-key")
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["APP_PASSWORD"] = ""

from app import db, llm, timeutil  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.llm import ChatResult, ToolCall  # noqa: E402

# Monday 2026-10-19 10:00 (Jalali 1405/07/27)
FIXED_NOW = datetime(2026, 10, 19, 10, 0)


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def clock(monkeypatch):
    c = Clock(FIXED_NOW)
    monkeypatch.setattr(timeutil, "now", c)
    return c


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch, clock):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    db.init_engine()
    yield
    get_settings.cache_clear()


class FakeLLM:
    """Scripted LLM: each entry is a ChatResult, or a callable(messages, tools) -> ChatResult."""

    configured = True

    def __init__(self, script=None, default="باشه 👌"):
        self.script = list(script or [])
        self.calls: list[dict] = []
        self.default = default

    async def chat(self, messages, tools=None, model=None):
        self.calls.append({"messages": messages, "tools": tools, "model": model})
        if self.script:
            step = self.script.pop(0)
            res = step(messages, tools) if callable(step) else step
        else:
            res = ChatResult(content=self.default)
        if res.tool_calls and not res.raw_message:
            res.raw_message = {
                "role": "assistant",
                "content": res.content,
                "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                    for c in res.tool_calls
                ],
            }
        return res

    async def complete(self, system, user, model=None):
        res = await self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], model=model)
        return res.content

    async def vision(self, image, mime, prompt, model=None):
        return "این یک برنامه کلاسی است."

    async def transcribe(self, audio, filename):
        return "فردا ساعت ده کلاس دارم"


def call(name, **args):
    return ChatResult(content="", tool_calls=[ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(args, ensure_ascii=False))])


@pytest.fixture
def fake_llm():
    f = FakeLLM()
    llm.set_llm(f)
    yield f
    llm.set_llm(None)
