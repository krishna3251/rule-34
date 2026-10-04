from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("discord_bot")


@dataclass(slots=True)
class MikoAIResponse:
    """One Groq turn, including local function calls requested by the model."""

    text: str
    assistant_message: dict[str, Any]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    web_search_used: bool = False


class MikoAI:
    """Groq provider with built-in web search and local tool calling."""

    WEB_SEARCH_MODELS = frozenset(
        {
            "openai/gpt-oss-20b",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-safeguard-20b",
        }
    )

    def __init__(self) -> None:
        self.api_key = os.getenv("GROQ_API_KEY", "").strip()
        self.model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
        self.web_search_enabled = (
            os.getenv("MIKO_WEB_SEARCH", "true").casefold().strip()
            not in {"0", "false", "no", "off"}
        )
        self.client: Any = None
        self._init_client()

    def _init_client(self) -> None:
        if not self.api_key:
            logger.warning("GROQ_API_KEY not found; Miko AI is unavailable")
            return

        try:
            from groq import Groq

            self.client = Groq(api_key=self.api_key)
            logger.info(
                "Miko Groq AI service initialized | model=%s web_search=%s",
                self.model,
                self.web_search_available,
            )
        except Exception as exc:
            logger.error(
                "Failed to initialize Miko Groq service: %s",
                exc,
                exc_info=True,
            )

    @property
    def ready(self) -> bool:
        return self.client is not None

    @property
    def web_search_available(self) -> bool:
        return (
            self.ready
            and self.web_search_enabled
            and self.model in self.WEB_SEARCH_MODELS
        )

    async def generate(
        self,
        messages: list[dict[str, str]],
        strict: bool = False,
    ) -> str:
        """Compatibility helper for direct command paths."""
        result = await self.generate_agent(
            messages,
            tool_schemas=[],
            allow_web=self.web_search_available,
            strict=strict,
        )
        return result.text or "Ara ara~ Main ek pal ke liye soch mein kho gayi thi."

    async def generate_agent(
        self,
        messages: list[dict[str, Any]],
        *,
        tool_schemas: list[dict[str, Any]] | None = None,
        allow_web: bool = True,
        strict: bool = False,
    ) -> MikoAIResponse:
        """Run one Groq turn with optional browser search and local tools.

        Groq's GPT-OSS models support server-side browser search alongside
        local function calling, so Miko can research current facts and invoke
        Discord automation from the same agent loop.
        """
        if not self.client:
            return MikoAIResponse(
                text="Main abhi Groq se connect nahi ho pa rahi hoon. Thodi der baad try karo.",
                assistant_message={
                    "role": "assistant",
                    "content": (
                        "Main abhi Groq se connect nahi ho pa rahi hoon. "
                        "Thodi der baad try karo."
                    ),
                },
            )

        request_messages = list(messages)

        if strict:
            request_messages.insert(
                1,
                {
                    "role": "system",
                    "content": (
                        "STRICT OUTPUT MODE: Keep the answer clean, non-explicit, short and in character. "
                        "Remember that Miko is female; use feminine self-reference in Hindi/Hinglish. "
                        "For Miko's own actions use forms such as 'karti hoon', 'karungi', 'gayi', "
                        "'rahi hoon', 'sakti hoon', 'thi', and 'meri'. "
                        "Do not use masculine self-forms such as 'karta hoon', 'karunga', 'gaya', "
                        "'raha hoon', 'sakta hoon', 'tha', or 'mera' for Miko."
                    ),
                },
            )

        tools: list[dict[str, Any]] = []
        if allow_web and self.web_search_available:
            tools.append({"type": "browser_search"})
        if tool_schemas:
            tools.extend(tool_schemas)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": request_messages,
            "temperature": 0.82 if not strict else 0.5,
            "max_completion_tokens": 400,
            "reasoning_effort": "low",
            "include_reasoning": False,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: self.client.chat.completions.create(**kwargs)
                ),
                timeout=35,
            )
            if not response.choices:
                return MikoAIResponse(
                    text="Ara ara~ Groq returned no answer.",
                    assistant_message={
                        "role": "assistant",
                        "content": "Ara ara~ Groq returned no answer.",
                    },
                )

            message = response.choices[0].message
            raw_calls = getattr(message, "tool_calls", None) or []
            tool_calls: list[dict[str, Any]] = []

            for raw_call in raw_calls:
                try:
                    args = json.loads(raw_call.function.arguments or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must be an object")

                    tool_calls.append(
                        {
                            "id": raw_call.id,
                            "name": raw_call.function.name,
                            "arguments": args,
                        }
                    )
                except Exception as exc:
                    logger.warning(
                        "Ignoring malformed Miko tool call: %s",
                        exc,
                    )

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": message.content,
            }
            if raw_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": raw_call.id,
                        "type": "function",
                        "function": {
                            "name": raw_call.function.name,
                            "arguments": raw_call.function.arguments or "{}",
                        },
                    }
                    for raw_call in raw_calls
                ]

            executed_tools = getattr(message, "executed_tools", None) or []
            web_search_used = bool(executed_tools)

            return MikoAIResponse(
                text=(message.content or "").strip()[:1800],
                assistant_message=assistant_message,
                tool_calls=tool_calls,
                web_search_used=web_search_used,
            )
        except asyncio.TimeoutError:
            logger.warning("Miko Groq request timed out")
            return MikoAIResponse(
                text="Thoda sa waqt do, darling. Main soch rahi hoon.",
                assistant_message={
                    "role": "assistant",
                    "content": "Thoda sa waqt do, darling. Main soch rahi hoon.",
                },
            )
        except Exception as exc:
            logger.error(
                "Miko Groq request failed: %s",
                exc,
                exc_info=True,
            )
            return MikoAIResponse(
                text="Meri shrine connection thodi nakhre kar rahi hai. Thodi der baad try karo.",
                assistant_message={
                    "role": "assistant",
                    "content": (
                        "Meri shrine connection thodi nakhre kar rahi hai. "
                        "Thodi der baad try karo."
                    ),
                },
            )
