"""LLM access through GapGPT (OpenAI-compatible API)."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from .config import get_settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
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


class LLM:
    def __init__(self) -> None:
        st = get_settings()
        self.settings = st
        self._client: AsyncOpenAI | None = None

    @property
    def configured(self) -> bool:
        return bool(self.settings.gapgpt_api_key)

    @property
    def client(self) -> AsyncOpenAI:
        if not self.configured:
            raise LLMError("کلید GAPGPT_API_KEY تنظیم نشده است.")
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.settings.gapgpt_api_key,
                base_url=self.settings.gapgpt_base_url,
                timeout=self.settings.llm_timeout,
                max_retries=2,
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> ChatResult:
        kwargs: dict[str, Any] = {"model": model or self.settings.chat_model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            resp = await self.client.chat.completions.create(**kwargs)
        except LLMError:
            raise
        except Exception as e:  # network / API errors
            log.exception("LLM chat failed")
            raise LLMError(f"خطا در ارتباط با مدل زبانی: {e}") from e
        msg = resp.choices[0].message
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (msg.tool_calls or [])
            if getattr(tc, "function", None)
        ]
        raw: dict[str, Any] = {"role": "assistant", "content": msg.content or ("" if not calls else None)}
        if calls:
            raw["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in calls
            ]
        return ChatResult(content=msg.content or "", tool_calls=calls, raw_message=raw)

    async def complete(self, system: str, user: str, model: str | None = None) -> str:
        res = await self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model or self.settings.fast_model,
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
        res = await self.chat(messages, model=model or self.settings.vision_model)
        return res.content.strip()

    async def transcribe(self, audio: bytes, filename: str) -> str:
        try:
            resp = await self.client.audio.transcriptions.create(
                model=self.settings.transcribe_model,
                file=(filename, audio),
                language="fa",
            )
        except LLMError:
            raise
        except Exception as e:
            log.exception("transcription failed")
            raise LLMError(f"تبدیل صدا به متن ناموفق بود: {e}") from e
        return (getattr(resp, "text", None) or str(resp)).strip()

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
