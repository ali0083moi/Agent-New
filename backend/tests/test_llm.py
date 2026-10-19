"""LLM layer against a mocked OpenAI-compatible HTTP API (no network)."""

import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI

from app import llm as llm_mod
from app import prefs
from app.db import LlmUsage, session


def _completion(model, content="سلام", tool_calls=None, usage=(100, 20)):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
        msg["content"] = None
    return {
        "id": "c1", "object": "chat.completion", "created": 1, "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)},
    }


def _sse(chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


class Server:
    def __init__(self):
        self.requests = []
        self.broken = set()

    def __call__(self, request: httpx.Request):
        path = request.url.path
        body = json.loads(request.content) if request.headers.get("content-type", "").startswith("application/json") else {}
        self.requests.append((path, body))
        if path.endswith("/chat/completions"):
            model = body["model"]
            if model in self.broken:
                return httpx.Response(503, json={"error": {"message": "overloaded"}})
            if body.get("stream"):
                base = {"id": "s", "object": "chat.completion.chunk", "created": 1, "model": model}
                if body.get("tools") and "tool" in json.dumps(body["messages"][-1], ensure_ascii=False):
                    return _sse([
                        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
                            {"index": 0, "id": "t1", "type": "function", "function": {"name": "add_task", "arguments": ""}}]}}]},
                        {**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"title\":"}}]}}]},
                        {**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": " \"x\"}"}}]}}]},
                        {**base, "choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55}},
                    ])
                return _sse([
                    {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "سلا"}}]},
                    {**base, "choices": [{"index": 0, "delta": {"content": "م!"}}]},
                    {**base, "choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 4, "total_tokens": 44}},
                ])
            return httpx.Response(200, json=_completion(model))
        if path.endswith("/embeddings"):
            inputs = body["input"]
            data = [{"object": "embedding", "index": i, "embedding": [float(len(t)), 1.0, 0.0]} for i, t in enumerate(inputs)]
            return httpx.Response(200, json={"object": "list", "data": data, "model": body["model"],
                                             "usage": {"prompt_tokens": 10, "total_tokens": 10}})
        if path.endswith("/audio/speech"):
            return httpx.Response(200, content=b"OggS-fake", headers={"content-type": "audio/ogg"})
        if path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "سلام دنیا"})
        return httpx.Response(404, json={"error": {"message": "nope"}})


@pytest.fixture
def server(monkeypatch):
    srv = Server()
    real = llm_mod.LLM()
    real._client = AsyncOpenAI(api_key="k", base_url="http://mock/v1", max_retries=0,
                               http_client=httpx.AsyncClient(transport=httpx.MockTransport(srv)))
    llm_mod.set_llm(real)
    yield srv, real
    llm_mod.set_llm(None)


def run(c):
    return asyncio.run(c)


def test_chat_records_usage_and_cost(server):
    srv, llm = server
    res = run(llm.chat([{"role": "user", "content": "hi"}], model="gpt-4.1"))
    assert res.content == "سلام" and res.model == "gpt-4.1"
    with session() as s:
        u = s.query(LlmUsage).one()
        assert (u.prompt_tokens, u.completion_tokens) == (100, 20)
        assert u.cost_usd == pytest.approx((100 * 2 + 20 * 8) / 1e6)


def test_fallback_to_next_model(server):
    srv, llm = server
    srv.broken.add("gpt-4.1")
    res = run(llm.chat([{"role": "user", "content": "hi"}], model="gpt-4.1"))
    assert res.model == "gpt-4o"
    with session() as s:
        rows = s.query(LlmUsage).order_by(LlmUsage.id).all()
        assert [(r.model, r.ok) for r in rows] == [("gpt-4.1", False), ("gpt-4o", True)]


def test_streaming_text_and_tool_calls(server):
    srv, llm = server
    seen = []

    async def on_delta(t):
        seen.append(t)

    res = run(llm.chat([{"role": "user", "content": "hi"}], model="gpt-4.1", on_delta=on_delta))
    assert res.content == "سلام!" and seen == ["سلا", "سلام!"]
    res = run(llm.chat([{"role": "user", "content": "use a tool"}], tools=[{"type": "function", "function": {
        "name": "add_task", "parameters": {"type": "object", "properties": {}}}}], model="gpt-4.1", on_delta=on_delta))
    assert res.tool_calls[0].name == "add_task"
    assert json.loads(res.tool_calls[0].arguments) == {"title": "x"}


def test_budget_degrades_and_blocks_background(server):
    srv, llm = server
    prefs.set("daily_budget_usd", 0.0001)
    run(llm.chat([{"role": "user", "content": "hi"}], model="gpt-4.1"))  # spends > budget
    assert llm.budget()["over"]
    res = run(llm.chat([{"role": "user", "content": "hi"}], model="gpt-4.1"))
    assert res.model == llm.model("fast")
    with pytest.raises(llm_mod.BudgetExceeded):
        run(llm.chat([{"role": "user", "content": "x"}], purpose="summary"))


def test_model_override_from_prefs(server):
    _, llm = server
    prefs.set("llm_chat_model", "claude-sonnet-5-5")
    assert llm.model("chat") == "claude-sonnet-5-5"
    assert llm.model("ielts") == "claude-sonnet-5-5"


def test_embed_speak_transcribe(server):
    _, llm = server
    vecs = run(llm.embed(["ab", "abcd"]))
    assert vecs[0][0] == 2.0 and vecs[1][0] == 4.0
    assert run(llm.speak("سلام")) == b"OggS-fake"
    assert run(llm.transcribe(b"\x00" * 100, "v.ogg")) == "سلام دنیا"


def test_price_lookup():
    assert llm_mod.price_for("gapgpt/gpt-4.1") == (2.0, 8.0)
    assert llm_mod.price_for("unknown-model") == llm_mod.DEFAULT_PRICE
