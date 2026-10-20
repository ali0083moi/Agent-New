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
os.environ["BACKGROUND_JOBS"] = "false"
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

    def model(self, kind):
        return {"chat": "fake-chat", "fast": "fake-fast", "vision": "fake-vision", "ielts": "fake-ielts"}[kind]

    def budget(self):
        return {"spent_today": 0.0, "limit": 0, "over": False}

    async def chat(self, messages, tools=None, model=None, purpose="chat", on_delta=None):
        self.calls.append({"messages": list(messages), "tools": tools, "model": model, "purpose": purpose})
        if self.script:
            step = self.script.pop(0)
            res = step(messages, tools) if callable(step) else step
        else:
            res = ChatResult(content=self.default)
        if on_delta is not None and res.content and not res.tool_calls:
            await on_delta(res.content)
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

    async def complete(self, system, user, model=None, purpose="compose"):
        res = await self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], model=model,
                              purpose=purpose)
        return res.content

    async def embed(self, texts):
        from app.llm import LLMError

        raise LLMError("no embeddings in tests")

    async def speak(self, text, fmt="opus"):
        return b"OggS"

    async def vision(self, image, mime, prompt, model=None):
        return "این یک برنامه کلاسی است."

    async def transcribe(self, audio, filename, language="fa"):
        self.last_language = language
        return "فردا ساعت ده کلاس دارم"


def call(name, **args):
    return ChatResult(content="", tool_calls=[ToolCall(id=f"call_{name}", name=name, arguments=json.dumps(args, ensure_ascii=False))])


@pytest.fixture
def fake_llm():
    f = FakeLLM()
    llm.set_llm(f)
    yield f
    llm.set_llm(None)
