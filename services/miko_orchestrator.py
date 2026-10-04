from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from services.miko_ai import MikoAI
from services.miko_context import MikoContextBuilder
from services.miko_gate import MikoGate
from services.miko_memory import MikoMemory
from services.miko_profile import MikoProfile
from services.miko_response import MikoResponseProcessor
from services.miko_router import MikoRouter

logger = logging.getLogger("discord_bot")


@dataclass(slots=True)
class MikoResult:
    replied: bool
    text: str | None = None
    reason: str = ""


class MikoOrchestrator:
    """Central coordinator. Feature logic belongs to dedicated services."""

    def __init__(self) -> None:
        self.gate = MikoGate()
        self.memory = MikoMemory()
        self.profile = MikoProfile()
        self.router = MikoRouter()
        self.context = MikoContextBuilder()
        self.ai = MikoAI()
        self.response = MikoResponseProcessor()

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

        await self.gate.acquire_ai_slot()
        try:
            reply = await self.ai.generate(
                messages,
                strict=decision.reason == "safety_refusal",
            )
        finally:
            self.gate.release_ai_slot()

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
                retry = await self.ai.generate(strict_messages, strict=True)
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
    def model(self) -> str:
        return self.ai.model

    def reset_memory(self, user_id: int, guild_id: int | None, channel_id: int) -> None:
        self.memory.reset(user_id, guild_id, channel_id)

    def forget_user(self, user_id: int) -> None:
        self.memory.reset(user_id)
        self.profile.forget(user_id)
