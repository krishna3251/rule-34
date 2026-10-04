from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import aiohttp

logger = logging.getLogger("discord_bot")


@dataclass(slots=True)
class MikoAIResponse:
    text: str
    assistant_message: dict[str, Any]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    web_search_used: bool = False
    provider: str = ""
    model: str = ""


class MikoAI:
    """OpenRouter-primary conversational AI with Groq fallback."""

    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self) -> None:
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        self.openrouter_model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()
        self.groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
        self.groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
        self.web_search_enabled = os.getenv("MIKO_WEB_SEARCH", "true").casefold().strip() not in {
            "0", "false", "no", "off"
        }
        self.temperature = self._env_float("MIKO_TEMPERATURE", 0.90, 0.2, 1.2)
        self.strict_temperature = self._env_float("MIKO_STRICT_TEMPERATURE", 0.45, 0.1, 0.9)
        self.groq_client: Any = None
        self._init_groq_client()

    @staticmethod
    def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
        try:
            return max(minimum, min(maximum, float(os.getenv(name, str(default)))))
        except (TypeError, ValueError):
            return default

    def _init_groq_client(self) -> None:
        if not self.groq_api_key:
            return
        try:
            from groq import Groq
            self.groq_client = Groq(api_key=self.groq_api_key)
        except Exception as exc:
            logger.error("Failed to initialize Groq: %s", exc, exc_info=True)

    @property
    def ready(self) -> bool:
        return bool(self.openrouter_api_key or self.groq_client)

    @property
    def web_search_available(self) -> bool:
        return bool(self.openrouter_api_key and self.web_search_enabled)

    @property
    def model(self) -> str:
        return self.openrouter_model if self.openrouter_api_key else self.groq_model

    @property
    def provider(self) -> str:
        if self.openrouter_api_key:
            return "openrouter"
        if self.groq_client:
            return "groq"
        return "none"

    async def generate(
        self,
        messages: list[dict[str, Any]],
        strict: bool = False,
        temperature: float | None = None,
    ) -> str:
        result = await self.generate_agent(
            messages,
            tool_schemas=[],
            allow_web=self.web_search_available,
            strict=strict,
            temperature=temperature,
        )
        return result.text or "Ara ara~ Main ek pal ke liye soch mein kho gayi thi."

    async def generate_agent(
        self,
        messages: list[dict[str, Any]],
        *,
        tool_schemas: list[dict[str, Any]] | None = None,
        allow_web: bool = True,
        strict: bool = False,
        temperature: float | None = None,
    ) -> MikoAIResponse:
        temp = (
            self.strict_temperature if strict else self.temperature
        ) if temperature is None else max(0.1, min(1.2, float(temperature)))

        if self.openrouter_api_key:
            try:
                return await self._openrouter_generate(
                    messages,
                    tool_schemas=tool_schemas,
                    allow_web=allow_web,
                    strict=strict,
                    temperature=temp,
                )
            except Exception as exc:
                logger.warning("OpenRouter failed; using Groq: %s", exc, exc_info=True)

        if self.groq_client:
            try:
                return await self._groq_generate(
                    messages,
                    tool_schemas=tool_schemas,
                    allow_web=allow_web,
                    strict=strict,
                    temperature=temp,
                )
            except Exception as exc:
                logger.error("Groq failed: %s", exc, exc_info=True)

        return MikoAIResponse(
            text="Main abhi AI provider se connect nahi ho pa rahi hoon. Thodi der baad try karo.",
            assistant_message={
                "role": "assistant",
                "content": "Main abhi AI provider se connect nahi ho pa rahi hoon. Thodi der baad try karo.",
            },
            provider="none",
            model="",
        )

    @staticmethod
    def _strict_message() -> dict[str, str]:
        return {
            "role": "system",
            "content": (
                "STRICT OUTPUT MODE: Keep the reply clean, concise, non-graphic and in character. "
                "Miko is female and uses feminine self-reference in Hindi/Hinglish."
            ),
        }

    async def _openrouter_generate(
        self,
        messages: list[dict[str, Any]],
        *,
        tool_schemas: list[dict[str, Any]] | None,
        allow_web: bool,
        strict: bool,
        temperature: float,
    ) -> MikoAIResponse:
        request_messages = list(messages)
        if strict:
            request_messages.insert(1, self._strict_message())

        tools: list[dict[str, Any]] = []
        if allow_web and self.web_search_enabled:
            tools.append({
                "type": "openrouter:web_search",
                "parameters": {
                    "engine": "auto",
                    "max_results": 5,
                    "max_total_results": 12,
                },
            })
        if tool_schemas:
            tools.extend(tool_schemas)

        payload: dict[str, Any] = {
            "model": self.openrouter_model,
            "messages": request_messages,
            "temperature": temperature,
            "max_completion_tokens": 450,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        site_url = os.getenv("OPENROUTER_SITE_URL", "").strip()
        title = os.getenv("OPENROUTER_APP_TITLE", "Miko Discord Bot").strip()
        if site_url:
            headers["HTTP-Referer"] = site_url
        if title:
            headers["X-OpenRouter-Title"] = title

        timeout = aiohttp.ClientTimeout(total=35)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.OPENROUTER_URL, json=payload, headers=headers) as response:
                body = await response.json(content_type=None)
                if response.status >= 400:
                    error = body.get("error", {}) if isinstance(body, dict) else {}
                    message = error.get("message") if isinstance(error, dict) else None
                    raise RuntimeError(f"OpenRouter HTTP {response.status}: {message or str(body)[:600]}")

        return self._parse_provider_response(body, provider="openrouter", model=self.openrouter_model)

    async def _groq_generate(
        self,
        messages: list[dict[str, Any]],
        *,
        tool_schemas: list[dict[str, Any]] | None,
        allow_web: bool,
        strict: bool,
        temperature: float,
    ) -> MikoAIResponse:
        request_messages = list(messages)
        if strict:
            request_messages.insert(1, self._strict_message())

        tools = list(tool_schemas or [])
        use_groq_web = bool(allow_web and self.web_search_enabled and not tools)

        kwargs: dict[str, Any] = {
            "model": self.groq_model,
            "messages": request_messages,
            "temperature": temperature,
            "max_completion_tokens": 450,
            "reasoning_effort": os.getenv("MIKO_REASONING_EFFORT", "low"),
            "include_reasoning": False,
        }
        if use_groq_web:
            kwargs["tools"] = [{"type": "browser_search"}]
            kwargs["tool_choice"] = "auto"
        elif tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await asyncio.wait_for(
            asyncio.to_thread(lambda: self.groq_client.chat.completions.create(**kwargs)),
            timeout=35,
        )
        return self._parse_provider_response(response, provider="groq", model=self.groq_model)

    @staticmethod
    def _parse_provider_response(
        response: Any,
        *,
        provider: str,
        model: str,
    ) -> MikoAIResponse:
        choices = response.get("choices") if isinstance(response, dict) else getattr(response, "choices", None)
        if not choices:
            raise RuntimeError(f"{provider} returned no choices")

        choice = choices[0]
        message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)
        if message is None:
            raise RuntimeError(f"{provider} returned no assistant message")

        if isinstance(message, dict):
            content = message.get("content")
            raw_calls = message.get("tool_calls") or []
        else:
            content = getattr(message, "content", None)
            raw_calls = getattr(message, "tool_calls", None) or []

        tool_calls: list[dict[str, Any]] = []
        normalized_raw_calls: list[dict[str, Any]] = []

        for raw_call in raw_calls:
            if isinstance(raw_call, dict):
                call_id = raw_call.get("id", "")
                function = raw_call.get("function", {}) or {}
                name = function.get("name", "")
                arguments_raw = function.get("arguments", "{}")
            else:
                call_id = getattr(raw_call, "id", "")
                function = getattr(raw_call, "function", None)
                name = getattr(function, "name", "")
                arguments_raw = getattr(function, "arguments", "{}")

            try:
                arguments = json.loads(arguments_raw or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
            except Exception as exc:
                logger.warning("Ignoring malformed %s tool call: %s", provider, exc)
                continue

            tool_calls.append({
                "id": str(call_id),
                "name": str(name),
                "arguments": arguments,
            })
            normalized_raw_calls.append({
                "id": str(call_id),
                "type": "function",
                "function": {
                    "name": str(name),
                    "arguments": arguments_raw or "{}",
                },
            })

        assistant_message = {"role": "assistant", "content": content}
        if normalized_raw_calls:
            assistant_message["tool_calls"] = normalized_raw_calls

        text = str(content or "").strip()[:1800]
        if not text and not tool_calls:
            raise RuntimeError(f"{provider} returned an empty response")

        return MikoAIResponse(
            text=text,
            assistant_message=assistant_message,
            tool_calls=tool_calls,
            provider=provider,
            model=model,
        )
