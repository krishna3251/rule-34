from __future__ import annotations

import asyncio
import json
import logging
import os
import math
import inspect
import time
from collections import Counter, deque
from contextvars import ContextVar
from datetime import datetime, timezone

from services.brain.config import BrainConfig
from services.brain.local import LocalModel
from dataclasses import dataclass, field
from typing import Any

import aiohttp

allow_external = ContextVar("miko_allow_external", default=True)
prefer_local = ContextVar("miko_prefer_local", default=True)
provider_usage = ContextVar("miko_provider_usage", default=None)

logger = logging.getLogger("discord_bot")


class RetryableProviderError(aiohttp.ClientError):
    def __init__(self, status, retry_after=.2):
        super().__init__(f'Provider HTTP {status}')
        self.retry_after = retry_after


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

    def __init__(self, config: BrainConfig | None = None) -> None:
        self.config = config or BrainConfig.from_env()
        self.local = None
        if self.config.local_enabled and self.config.local_model:
            try:
                self.local = LocalModel(self.config)
            except ValueError:
                logger.warning('Local inference configuration is invalid; continuing without it')
        self.session = None
        self.operations = Counter()
        self.timings = deque(maxlen=200)
        self._budget_day = ''
        self._budget_used = 0
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
            value = float(os.getenv(name, str(default)))
            return max(minimum, min(maximum, value)) if math.isfinite(value) else default
        except (TypeError, ValueError):
            return default

    def _init_groq_client(self) -> None:
        if not self.groq_api_key or not self.config.external_enabled:
            return
        try:
            from groq import AsyncGroq
            self.groq_client = AsyncGroq(api_key=self.groq_api_key, timeout=self.config.provider_timeout, max_retries=0)
        except Exception as exc:
            logger.error("Failed to initialize Groq: %s", type(exc).__name__)

    @property
    def ready(self) -> bool:
        return bool(self.local or (self.config.external_enabled and (self.openrouter_api_key or self.groq_client)))

    @property
    def web_search_available(self) -> bool:
        return bool(self.config.external_enabled and (self.openrouter_api_key or self.groq_client) and self.web_search_enabled)

    @property
    def model(self) -> str:
        return self.config.local_model if self.local else (self.openrouter_model if self.openrouter_api_key else self.groq_model)

    @property
    def provider(self) -> str:
        if self.local:
            return "local"
        if not self.config.external_enabled:
            return "none"
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
        if sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) > self.config.context_chars:
            raise ValueError('Model context exceeds configured limit')
        temp = (
            self.strict_temperature if strict else self.temperature
        ) if temperature is None else max(0.1, min(1.2, float(temperature)))

        # A local model has no network-search or action capability. Tool results
        # are not rerouted to it mid-plan. Privacy choices apply to every retry.
        if self.local and prefer_local.get() and not allow_web and not any(m.get('role') == 'tool' for m in messages):
            try:
                self._count_operation('local')
                started = time.monotonic()
                request_messages = list(messages)
                if strict:
                    request_messages.insert(1, self._strict_message())
                body = await self.local.generate(request_messages, temp)
                self.timings.append(('local', (time.monotonic()-started)*1000))
                return self._parse_provider_response(body, provider='local', model=self.config.local_model)
            except Exception as exc:
                self.operations['local_errors'] += 1
                logger.warning('Local inference failed: %s', type(exc).__name__)

        if self.config.external_enabled and allow_external.get():
            for provider, configured, method in [
                    ('openrouter', self.openrouter_api_key, self._openrouter_generate),
                    ('groq', self.groq_client, self._groq_generate)]:
                if not configured:
                    continue
                # Bound retries and daily provider operations, including repair/tool turns.
                for attempt in range(2):
                    if not self._reserve_budget():
                        return self._unavailable()
                    self._count_operation(provider)
                    started = time.monotonic()
                    try:
                        result = await method(messages, tool_schemas=tool_schemas,
                                              allow_web=allow_web, strict=strict, temperature=temp)
                        self.timings.append((provider, (time.monotonic()-started)*1000))
                        return result
                    except Exception as exc:
                        self.operations[provider + '_errors'] += 1
                        logger.warning('Miko %s failed; using Groq backup when available: %s', provider, type(exc).__name__)
                        if attempt or not isinstance(exc, aiohttp.ClientError):
                            break
                        delay = getattr(exc, 'retry_after', .2)
                        if delay > 2:
                            break  # Switch provider instead of ignoring a long Retry-After.
                        await asyncio.sleep(delay)
        return self._unavailable()

    def _reserve_budget(self):
        day = datetime.now(timezone.utc).date().isoformat()
        if day != self._budget_day:
            self._budget_day, self._budget_used = day, 0
        if self._budget_used >= self.config.provider_daily_limit:
            self.operations['budget_rejected'] += 1
            return False
        self._budget_used += 1
        return True

    def _count_operation(self, provider):
        self.operations[provider] += 1
        usage = provider_usage.get()
        if usage is not None:
            usage[provider] += 1

    @staticmethod
    def _unavailable():
        text = 'Conversation AI is unavailable for this request. Local skills and normal bot commands still work.'
        return MikoAIResponse(text=text, assistant_message={'role': 'assistant', 'content': text}, provider='none')

    async def close(self):
        if self.session is not None:
            await self.session.close()
        if self.local is not None:
            await self.local.close()
        if self.groq_client is not None:
            result = self.groq_client.close()
            if inspect.isawaitable(result):
                await result

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

        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.config.provider_timeout))
        async with self.session.post(self.OPENROUTER_URL, json=payload, headers=headers, allow_redirects=False) as response:
            if response.status != 200:
                if response.status in {429, 500, 502, 503, 504}:
                    try:
                        delay = float(response.headers.get('Retry-After', '.2'))
                        delay = max(.2, delay) if math.isfinite(delay) else 3
                    except ValueError:
                        delay = 3  # HTTP-date Retry-After: do not retry immediately.
                    raise RetryableProviderError(response.status, delay)
                raise RuntimeError(f'OpenRouter HTTP {response.status}')
            chunks = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                chunks.extend(chunk)
                if len(chunks) > 262144:
                    raise ValueError('Provider response exceeds limit')
            body = json.loads(chunks)

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
        use_groq_web = bool(allow_web and self.web_search_enabled)
        if use_groq_web:
            # Groq executes browser_search server-side, so it can coexist with
            # Miko's local Discord tools. Previously local tools accidentally
            # disabled web search on the fallback provider.
            tools.insert(0, {"type": "browser_search"})

        kwargs: dict[str, Any] = {
            "model": self.groq_model,
            "messages": request_messages,
            "temperature": temperature,
            "max_completion_tokens": 450,
            "reasoning_effort": os.getenv("MIKO_REASONING_EFFORT", "low"),
            "include_reasoning": False,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        create = self.groq_client.chat.completions.create
        # Production uses AsyncGroq; synchronous injected clients remain supported.
        if inspect.iscoroutinefunction(create):
            response = await asyncio.wait_for(create(**kwargs), timeout=self.config.provider_timeout)
        else:
            from services.brain.runtime import worker
            response = await worker(create, **kwargs)
            if inspect.isawaitable(response):
                response = await response
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

        if len(raw_calls) > 8:
            raise ValueError("Too many provider tool calls")
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
                if not isinstance(arguments_raw, str) or len(arguments_raw) > 5000:
                    raise ValueError("Tool arguments exceed limits")
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

        if content is not None and not isinstance(content, str):
            raise ValueError("Provider content must be text")
        text = str(content or "").strip()[:1800]
        if not text and not tool_calls:
            raise RuntimeError(f"{provider} returned an empty response")

        return MikoAIResponse(
            text=text,
            assistant_message=assistant_message,
            tool_calls=tool_calls,
            web_search_used=bool(
                (message.get('annotations') if isinstance(message, dict) else getattr(message, 'annotations', None))
                or (response.get('executed_tools') if isinstance(response, dict) else getattr(response, 'executed_tools', None))
            ),
            provider=provider,
            model=model,
        )
