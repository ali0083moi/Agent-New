"""LLM access through GapGPT (OpenAI-compatible API).

Adds on top of the raw client: model fallbacks, usage/cost tracking with a daily budget,
streaming, embeddings, speech-to-text and text-to-speech.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx
import openai
from openai import AsyncOpenAI

from .config import get_settings

log = logging.getLogger(__name__)

# USD per 1M tokens (input, output) — from GapGPT's price list; override with MODEL_PRICES in .env
PRICES: dict[str, tuple[float, float]] = {
    "gpt-6.1-sol": (2.0, 10.0),
    "gpt-6-sol": (2.0, 10.0),
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6-astra": (10.0, 50.0),
    "gpt-5.6-sol": (2.5, 15.0),
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.6-terra": (2.0, 12.0),
    "gpt-5.5": (5.0, 30.0),
    "gpt-5.4": (2.5, 15.0),
    "gpt-5.4-mini": (0.75, 4.5),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.2": (1.75, 10.5),
    "gpt-5.1": (1.25, 10.0),
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "o3": (2.0, 8.0),
    "o4-mini": (1.1, 4.4),
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "gemini-4-argon": (2.0, 10.0),
    "gemini-3.5-flash": (1.5, 9.0),
    "gemini-3.1-pro-preview": (2.0, 12.0),
    "gemini-3.1-flash-lite": (0.25, 1.5),
    "gemini-2.5-flash": (0.30, 2.5),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "deepseek-v4-pro": (1.74, 3.48),
    "deepseek-v4-flash": (0.14, 0.28),
    "grok-4.3": (1.25, 2.5),
    "qwen3-235b-a22b": (0.16, 0.48),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
    "gpt-4o-mini-tts": (0.60, 12.0),
}
DEFAULT_PRICE = (2.0, 10.0)
TRANSCRIBE_USD_PER_MIN = 0.006  # rough estimate
TTS_USD_PER_1M_CHARS = 15.0  # rough estimate
BACKGROUND_PURPOSES = {"summary", "facts", "embed"}

DeltaCallback = Callable[[str], Awaitable[None]]


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string


@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict[str, Any] = field(default_factory=dict)
    model: str = ""


def price_for(model: str) -> tuple[float, float]:
    override = get_settings().model_prices.get(model)
    if override and len(override) >= 2:
        return float(override[0]), float(override[1])
    if model in PRICES:
        return PRICES[model]
    base = model.split("/")[-1]
    for name, price in PRICES.items():
        if base == name or base.startswith(name + "-"):
            return price
    return DEFAULT_PRICE


def cost_of(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = price_for(model)
    return (prompt_tokens * pin + completion_tokens * pout) / 1_000_000


def _approx_tokens(obj: Any) -> int:
    return max(1, len(str(obj)) // 3)


def _is_fatal(e: Exception) -> bool:
    """Errors that another model won't fix."""
    return isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError))


class LLM:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: AsyncOpenAI | None = None

    # ------------------------------------------------------------ setup
    @property
    def configured(self) -> bool:
        return bool(self.settings.gapgpt_api_key)

    @property
    def client(self) -> AsyncOpenAI:
        if not self.configured:
            raise LLMError("کلید GAPGPT_API_KEY تنظیم نشده است.")
        if self._client is None:
            http_client = None
            if self.settings.llm_proxy:
                http_client = httpx.AsyncClient(proxy=self.settings.llm_proxy, timeout=self.settings.llm_timeout)
            self._client = AsyncOpenAI(
                api_key=self.settings.gapgpt_api_key,
                base_url=self.settings.gapgpt_base_url,
                timeout=self.settings.llm_timeout,
                max_retries=1,
                http_client=http_client,
            )
        return self._client

    def model(self, kind: str) -> str:
        """Model for a role (chat|fast|vision|ielts); user overrides from Settings win over .env."""
        from . import prefs

        override = prefs.get(f"llm_{kind}_model") or ""
        if override:
            return override
        if kind == "ielts":
            return self.settings.ielts_model or self.model("chat")
        return getattr(self.settings, f"{kind}_model")

    @staticmethod
    def _split(value: str) -> list[str]:
        return [m.strip() for m in (value or "").split(",") if m.strip()]

    # ------------------------------------------------------------ usage & budget
    def _record(self, model: str, purpose: str, pt: int, ct: int, cost: float, ok: bool, started: float,
                error: str = "") -> None:
        from . import timeutil
        from .db import LlmUsage, session

        try:
            with session() as s:
                s.add(LlmUsage(day=timeutil.today(), model=model, purpose=purpose, prompt_tokens=pt,
                               completion_tokens=ct, cost_usd=round(cost, 6), ok=ok,
                               latency_ms=int((time.monotonic() - started) * 1000), error=error[:500]))
        except Exception:  # noqa: BLE001 - usage tracking must never break a reply
            log.exception("could not record usage")

    def budget(self) -> dict[str, Any]:
        from sqlalchemy import func

        from . import prefs, timeutil
        from .db import LlmUsage, session

        with session() as s:
            spent = s.query(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).filter(LlmUsage.day == timeutil.today()).scalar()
        limit = float(prefs.get("daily_budget_usd") or 0)
        return {"spent_today": float(spent or 0), "limit": limit, "over": bool(limit and spent >= limit)}

    # ------------------------------------------------------------ chat
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        purpose: str = "chat",
        on_delta: DeltaCallback | None = None,
    ) -> ChatResult:
        primary = model or self.model("chat")
        if self.budget()["over"]:
            if purpose in BACKGROUND_PURPOSES:
                raise BudgetExceeded("سقف هزینه روزانه پر شده است")
            primary = self.model("fast")  # degrade gracefully instead of going silent
        from . import prefs

        fallbacks = prefs.get("llm_fallback_models") or self.settings.fallback_models
        candidates = [primary] + [m for m in self._split(fallbacks) if m != primary]
        last: Exception | None = None
        for m in candidates:
            started = time.monotonic()
            try:
                res, pt, ct = await self._chat_once(m, messages, tools, on_delta)
                self._record(m, purpose, pt, ct, cost_of(m, pt, ct), True, started)
                if m != primary:
                    log.warning("served by fallback model %s", m)
                return res
            except Exception as e:  # noqa: BLE001
                last = e
                self._record(m, purpose, 0, 0, 0.0, False, started, error=str(e))
                log.warning("model %s failed: %s", m, e)
                if _is_fatal(e) or isinstance(e, LLMError):
                    break
        raise LLMError(f"خطا در ارتباط با مدل زبانی: {last}") from last

    async def _chat_once(self, model: str, messages, tools, on_delta) -> tuple[ChatResult, int, int]:
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if on_delta is not None and self.settings.llm_streaming:
            try:
                return await self._stream(kwargs, on_delta)
            except (openai.BadRequestError, openai.UnprocessableEntityError) as e:
                log.info("streaming not accepted by %s (%s); retrying without", model, e)
        resp = await self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
            if getattr(tc, "function", None)
        ]
        usage = getattr(resp, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) or _approx_tokens(messages)
        ct = getattr(usage, "completion_tokens", 0) or _approx_tokens(msg.content or "") + _approx_tokens(calls)
        return self._result(model, msg.content or "", calls), pt, ct

    async def _stream(self, kwargs: dict[str, Any], on_delta: DeltaCallback) -> tuple[ChatResult, int, int]:
        stream = await self.client.chat.completions.create(**kwargs, stream=True, stream_options={"include_usage": True})
        text = ""
        slots: dict[int, dict[str, str]] = {}
        usage = None
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                text += delta.content
                if not slots:
                    await on_delta(text)
            for tc in delta.tool_calls or []:
                slot = slots.setdefault(tc.index or 0, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                fn = tc.function
                if fn is not None:
                    if fn.name and fn.name != slot["name"]:
                        slot["name"] += fn.name
                    if fn.arguments:
                        slot["arguments"] += fn.arguments
        calls = [
            ToolCall(id=v["id"] or f"call_{i}", name=v["name"], arguments=v["arguments"] or "{}")
            for i, v in sorted(slots.items())
            if v["name"]
        ]
        pt = getattr(usage, "prompt_tokens", 0) or _approx_tokens(kwargs["messages"])
        ct = getattr(usage, "completion_tokens", 0) or _approx_tokens(text) + _approx_tokens(calls)
        return self._result(kwargs["model"], text, calls), pt, ct

    @staticmethod
    def _result(model: str, content: str, calls: list[ToolCall]) -> ChatResult:
        raw: dict[str, Any] = {"role": "assistant", "content": content or ("" if not calls else None)}
        if calls:
            raw["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls
            ]
        return ChatResult(content=content, tool_calls=calls, raw_message=raw, model=model)

    async def complete(self, system: str, user: str, model: str | None = None, purpose: str = "compose") -> str:
        res = await self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model or self.model("fast"),
            purpose=purpose,
        )
        return res.content.strip()

    async def vision(self, image: bytes, mime: str, prompt: str, model: str | None = None) -> str:
        b64 = base64.b64encode(image).decode()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }
        ]
        res = await self.chat(messages, model=model or self.model("vision"), purpose="vision")
        return res.content.strip()

    # ------------------------------------------------------------ audio
    async def transcribe(self, audio: bytes, filename: str, language: str | None = "fa") -> str:
        models = [self.settings.transcribe_model] + self._split(self.settings.transcribe_fallback_models)
        last: Exception | None = None
        for m in dict.fromkeys(models):
            started = time.monotonic()
            try:
                extra = {"language": language} if language else {}
                resp = await self.client.audio.transcriptions.create(model=m, file=(filename, audio), **extra)
                minutes = len(audio) / 2000 / 60  # ~16 kbps opus
                self._record(m, "transcribe", 0, 0, minutes * TRANSCRIBE_USD_PER_MIN, True, started)
                return (getattr(resp, "text", None) or str(resp)).strip()
            except Exception as e:  # noqa: BLE001
                last = e
                self._record(m, "transcribe", 0, 0, 0.0, False, started, error=str(e))
                if _is_fatal(e) or isinstance(e, LLMError):
                    break
        raise LLMError(f"تبدیل صدا به متن ناموفق بود: {last}") from last

    async def speak(self, text: str, fmt: str = "opus") -> bytes:
        models = [self.settings.tts_model] + self._split(self.settings.tts_fallback_models)
        last: Exception | None = None
        for m in dict.fromkeys(models):
            started = time.monotonic()
            try:
                resp = await self.client.audio.speech.create(model=m, voice=self.settings.tts_voice, input=text,
                                                             response_format=fmt)
                data = resp.content if isinstance(getattr(resp, "content", None), bytes) else await resp.aread()
                self._record(m, "tts", 0, 0, len(text) * TTS_USD_PER_1M_CHARS / 1_000_000, True, started)
                return data
            except Exception as e:  # noqa: BLE001
                last = e
                self._record(m, "tts", 0, 0, 0.0, False, started, error=str(e))
                if _is_fatal(e) or isinstance(e, LLMError):
                    break
        raise LLMError(f"تبدیل متن به صدا ناموفق بود: {last}") from last

    # ------------------------------------------------------------ embeddings
    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.budget()["over"]:
            raise BudgetExceeded("سقف هزینه روزانه پر شده است")
        m = self.settings.embedding_model
        started = time.monotonic()
        try:
            resp = await self.client.embeddings.create(model=m, input=texts)
        except Exception as e:  # noqa: BLE001
            self._record(m, "embed", 0, 0, 0.0, False, started, error=str(e))
            raise LLMError(f"embedding failed: {e}") from e
        pt = getattr(getattr(resp, "usage", None), "prompt_tokens", 0) or _approx_tokens(texts)
        self._record(m, "embed", pt, 0, cost_of(m, pt, 0), True, started)
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]

    async def list_models(self) -> list[str]:
        try:
            page = await self.client.models.list()
            return sorted(m.id for m in page.data)
        except Exception as e:
            raise LLMError(str(e)) from e


_instance: LLM | None = None


def get_llm() -> LLM:
    global _instance
    if _instance is None:
        _instance = LLM()
    return _instance


def set_llm(llm: Any) -> None:
    """Swap the LLM implementation (used by tests)."""
    global _instance
    _instance = llm
