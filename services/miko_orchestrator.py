from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from services.miko_ai import MikoAI
from services.miko_memory import MikoMemory

logger = logging.getLogger("discord_bot")


@dataclass(slots=True)
class MikoDecision:
    respond: bool
    prompt: str = ""
    reason: str = ""


class MikoOrchestrator:
    """Rukiya-style orchestration layer.

    Decision engine -> Memory -> AI provider -> response.
    Each responsibility is isolated so the AI provider can change without
    rewriting Discord message handling.
    """

    RESPONSE_COOLDOWN = 1.0

    def __init__(self) -> None:
        self.memory = MikoMemory()
        self.ai = MikoAI()
        self.last_response: dict[int, float] = {}

    def extract_trigger(self, message: Any, bot: Any) -> str | None:
        """Return prompt only when the user explicitly summons Miko."""
        content = message.content.strip()

        if bot.user:
            for token in (f"<@{bot.user.id}>", f"<@!{bot.user.id}>"):
                if token in content:
                    return content.replace(token, "").strip()

        lowered = content.casefold()
        if lowered == "miko":
            return ""

        for prefix in ("miko ", "miko,", "miko:", "miko -"):
            if lowered.startswith(prefix):
                return content[len(prefix):].strip(" ,:~-")

        return None

    def decide(self, message: Any, bot: Any, auto_chat: bool = False) -> MikoDecision:
        if getattr(message.author, "bot", False):
            return MikoDecision(False, reason="bot_message")

        prompt = self.extract_trigger(message, bot)
        if prompt is None and not auto_chat:
            return MikoDecision(False, reason="not_summoned")

        user_id = message.author.id
        now = time.monotonic()
        if now - self.last_response.get(user_id, 0) < self.RESPONSE_COOLDOWN:
            return MikoDecision(False, reason="cooldown")

        self.last_response[user_id] = now

        if not prompt:
            prompt = (
                "The user has summoned you by saying your name. "
                "Greet them naturally and invite them to talk."
            )

        return MikoDecision(
            True,
            prompt=prompt,
            reason="auto_chat" if auto_chat else "summoned",
        )

    async def handle(self, message: Any, bot: Any, auto_chat: bool = False) -> str | None:
        decision = self.decide(message, bot, auto_chat=auto_chat)
        if not decision.respond:
            return None

        guild_id = getattr(message.guild, "id", None)
        channel_id = message.channel.id

        # Read history first so the current message is not duplicated in the prompt.
        history = self.memory.history(message.author.id, guild_id, channel_id)
        self.memory.add(
            message.author.id,
            "user",
            decision.prompt,
            guild_id,
            channel_id,
        )

        reply = await self.ai.generate(decision.prompt, history)

        self.memory.add(
            message.author.id,
            "assistant",
            reply,
            guild_id,
            channel_id,
        )
        return reply

    def reset_memory(self, user_id: int, guild_id: int | None, channel_id: int) -> None:
        self.memory.reset(user_id, guild_id, channel_id)

    @property
    def ai_ready(self) -> bool:
        return self.ai.ready

    @property
    def model(self) -> str:
        return self.ai.model
