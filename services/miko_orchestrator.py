from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from services.miko_ai import MikoAI
from services.miko_context import MikoContextBuilder
from services.miko_gate import MikoGate
from services.miko_memory import MikoMemory
from services.miko_profile import MikoProfile
from services.miko_response import MikoResponseProcessor
from services.miko_router import MikoRouter
from services.miko_tools import MikoToolContext, MikoToolRegistry

logger = logging.getLogger("discord_bot")


@dataclass(slots=True)
class MikoResult:
    replied: bool
    text: str | None = None
    reason: str = ""


class MikoOrchestrator:
    """Central coordinator for Miko's conversational agent loop."""

    def __init__(self) -> None:
        self.gate = MikoGate()
        self.memory = MikoMemory()
        self.profile = MikoProfile()
        self.router = MikoRouter()
        self.context = MikoContextBuilder()
        self.ai = MikoAI()
        self.response = MikoResponseProcessor()
        self.tools = MikoToolRegistry()
        self.miko_chat: Any | None = None

        try:
            self.max_tool_iterations = max(
                1,
                min(int(os.getenv("MIKO_MAX_TOOL_ITERATIONS", "3")), 5),
            )
        except (TypeError, ValueError):
            self.max_tool_iterations = 3

        try:
            self.max_tool_calls = max(
                1,
                min(int(os.getenv("MIKO_MAX_TOOL_CALLS", "3")), 8),
            )
        except (TypeError, ValueError):
            self.max_tool_calls = 3

    def bind_chat_cog(self, cog: Any) -> None:
        """Bind the Discord adapter for future Miko-specific tool extensions."""
        self.miko_chat = cog

    async def is_candidate(
        self,
        message: Any,
        bot: Any,
        *,
        auto_chat: bool = False,
        quiet: bool = False,
    ) -> bool:
        return await self.gate.is_candidate(
            message,
            bot,
            auto_chat=auto_chat,
            quiet=quiet,
        )

    async def _agent_generate(
        self,
        messages: list[dict[str, Any]],
        *,
        bot: Any,
        message: Any,
        allow_actions: bool,
        strict: bool = False,
        allow_web: bool = True,
    ) -> tuple[str, bool]:
        """Run bounded local-tool automation plus Groq's server-side web search."""
        working = list(messages)
        used_web = False
        total_tool_calls = 0

        tool_schemas = self.tools.schemas()

        for _ in range(self.max_tool_iterations):
            ai_result = await self.ai.generate_agent(
                working,
                tool_schemas=tool_schemas,
                allow_web=allow_web,
                strict=strict,
            )
            used_web = used_web or ai_result.web_search_used

            if not ai_result.tool_calls:
                return ai_result.text.strip(), used_web

            if (
                total_tool_calls + len(ai_result.tool_calls)
                > self.max_tool_calls
            ):
                logger.warning(
                    "Miko tool-call budget exceeded user=%s",
                    getattr(message.author, "id", None),
                )
                return (
                    "I stopped the automation before it could run too many actions.",
                    used_web,
                )

            working.append(ai_result.assistant_message)

            for call in ai_result.tool_calls:
                name = str(call.get("name") or "")
                arguments = call.get("arguments") or {}
                context = MikoToolContext(
                    bot=bot,
                    message=message,
                    miko_chat=self.miko_chat,
                    allow_actions=allow_actions,
                )

                result = await self.tools.execute(
                    name,
                    arguments if isinstance(arguments, dict) else {},
                    context,
                )
                total_tool_calls += 1

                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or ""),
                        "name": name,
                        "content": json.dumps(
                            result,
                            ensure_ascii=False,
                            default=str,
                        )[:5000],
                    }
                )

        return (
            "I stopped after reaching the automation limit for this request.",
            used_web,
        )

    async def handle(
        self,
        message: Any,
        bot: Any,
        *,
        auto_chat: bool = False,
        admin_level: int = 1,
        disabled: bool = False,
        quiet: bool = False,
    ) -> MikoResult:
        if disabled:
            return MikoResult(False, reason="channel_disabled")

        if quiet:
            trigger = self.gate._extract_trigger(message, bot)
            if trigger[1] is False:
                return MikoResult(False, reason="quiet_mode")

        profile = self.profile.get(message.author.id)
        user_level_cap = int(profile.get("level_cap", 2) or 2)

        decision = await self.gate.check(
            message,
            bot,
            auto_chat=auto_chat,
            admin_level=admin_level,
            user_level_cap=user_level_cap,
        )
        if not decision.respond:
            return MikoResult(False, reason=decision.reason)

        normalized_prompt = decision.prompt.casefold().strip()
        if normalized_prompt in {
            "forget me",
            "forget my memory",
            "delete my memory",
            "delete my data",
        }:
            self.forget_user(message.author.id)
            return MikoResult(
                True,
                "Your stored Miko memory and profile have been deleted.",
                reason="forget_request",
            )

        if decision.prompt.casefold() in {
            "stop flirting",
            "stop flirting please",
            "don't flirt",
            "dont flirt",
        }:
            self.profile.set(message.author.id, "level_cap", 0)
            decision.spice_level = 0
            decision.mood = "serious"

        history = self.memory.history(
            message.author.id,
            getattr(message.guild, "id", None),
            message.channel.id,
        )
        intent = self.router.classify(decision.prompt)
        profile = self.profile.get(message.author.id)

        self.memory.add(
            message.author.id,
            "user",
            decision.prompt,
            getattr(message.guild, "id", None),
            message.channel.id,
        )

        messages = self.context.build(
            prompt=decision.prompt,
            history=history,
            spice_level=decision.spice_level,
            mood=decision.mood,
            intent=intent,
            profile=profile,
            strict=decision.reason == "safety_refusal",
        )

        allow_actions = (
            decision.reason == "summoned"
            and decision.reason != "safety_refusal"
        )

        await self.gate.acquire_ai_slot()
        try:
            reply, _used_web = await self._agent_generate(
                messages,
                bot=bot,
                message=message,
                allow_actions=allow_actions,
                strict=decision.reason == "safety_refusal",
                allow_web=decision.reason != "safety_refusal",
            )
        finally:
            self.gate.release_ai_slot()

        if not reply:
            reply = "Ara ara~ Main ek pal ke liye soch mein kho gayi thi."

        valid, cleaned, validation_reason = self.response.validate(
            message.author.id,
            reply,
            decision.spice_level,
        )

        if not valid:
            strict_messages = self.context.build(
                prompt=decision.prompt,
                history=history,
                spice_level=0,
                mood="serious",
                intent=intent,
                profile=profile,
                strict=True,
            )

            await self.gate.acquire_ai_slot()
            try:
                retry, _ = await self._agent_generate(
                    strict_messages,
                    bot=bot,
                    message=message,
                    allow_actions=False,
                    strict=True,
                    allow_web=False,
                )
            finally:
                self.gate.release_ai_slot()

            retry_valid, retry_cleaned, retry_reason = self.response.validate(
                message.author.id,
                retry,
                0,
            )

            if retry_valid:
                cleaned = retry_cleaned
            else:
                logger.warning(
                    "Miko output rejected user=%s reason=%s retry=%s",
                    message.author.id,
                    validation_reason,
                    retry_reason,
                )
                cleaned = self.response.fallback(retry_reason)

        self.memory.add(
            message.author.id,
            "assistant",
            cleaned,
            getattr(message.guild, "id", None),
            message.channel.id,
        )

        return MikoResult(True, cleaned, reason=decision.reason)

    @property
    def ai_ready(self) -> bool:
        return self.ai.ready

    @property
    def provider(self) -> str:
        return self.ai.provider

    @property
    def model(self) -> str:
        return self.ai.model

    @property
    def web_search_ready(self) -> bool:
        return self.ai.web_search_available

    @property
    def tool_names(self) -> list[str]:
        return self.tools.names()

    def reset_memory(
        self,
        user_id: int,
        guild_id: int | None,
        channel_id: int,
    ) -> None:
        self.memory.reset(user_id, guild_id, channel_id)

    def forget_user(self, user_id: int) -> None:
        self.memory.reset(user_id)
        self.profile.forget(user_id)
