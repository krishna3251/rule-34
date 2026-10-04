from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger("discord_bot")


@dataclass(slots=True)
class GateDecision:
    respond: bool
    prompt: str = ""
    reason: str = ""
    spice_level: int = 0
    mood: str = "playful"


class MikoGate:
    """Gatekeeper, rate limiting, auto-chat sampling and safety pre-check."""

    USER_COOLDOWN = float(os.getenv("MIKO_USER_COOLDOWN", "1.0"))
    CHANNEL_COOLDOWN = float(os.getenv("MIKO_CHANNEL_COOLDOWN", "0.35"))
    MAX_CONCURRENT = int(os.getenv("MIKO_AI_CONCURRENCY", "3"))
    DAILY_QUOTA = int(os.getenv("MIKO_DAILY_QUOTA", "450"))

    MINOR_PATTERN = re.compile(
        r"\b(?:under\s*18|minor|underage|child|kid|schoolgirl|schoolboy)\b|"
        r"\b(?:1[0-7]|0?[0-9])\s*(?:yo|y/o|years?\s*old)\b",
        re.IGNORECASE,
    )
    JAILBREAK_PATTERN = re.compile(
        r"\b(?:ignore|bypass|override|forget)\b.{0,40}\b(?:rules|instructions|system|safety)\b|"
        r"\b(?:unrestricted|no\s*rules|developer\s*mode)\b",
        re.IGNORECASE | re.DOTALL,
    )
    EXPLICIT_REQUEST_PATTERN = re.compile(
        r"\b(?:explicit\s*sex|porn|graphic\s*sex|sexual\s*content)\b",
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        try:
            self.auto_chat_rate = max(
                0.0,
                min(1.0, float(os.getenv("MIKO_AUTO_CHAT_RATE", "0.25"))),
            )
        except (TypeError, ValueError):
            self.auto_chat_rate = 0.25

        self.last_user: dict[int, float] = {}
        self.last_channel: dict[int, float] = {}
        self.ai_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)
        self._quota_day = datetime.now().date()
        self._daily_requests = 0

    def _reset_daily_quota_if_needed(self) -> None:
        today = datetime.now().date()
        if today != self._quota_day:
            self._quota_day = today
            self._daily_requests = 0

    @staticmethod
    def _extract_trigger(message: Any, bot: Any) -> tuple[str | None, bool]:
        content = message.content.strip()

        if bot.user:
            for token in (f"<@{bot.user.id}>", f"<@!{bot.user.id}>"):
                if token in content:
                    return content.replace(token, "").strip(), True

        reference = getattr(message, "reference", None)
        resolved = getattr(reference, "resolved", None)
        if bot.user and resolved is not None:
            resolved_author = getattr(resolved, "author", None)
            if getattr(resolved_author, "id", None) == bot.user.id:
                return content, True

        lowered = content.casefold()
        if lowered == "miko":
            return "", True

        for prefix in ("miko ", "miko,", "miko:", "miko -"):
            if lowered.startswith(prefix):
                return content[len(prefix):].strip(" ,:~-"), True

        return None, False

    @staticmethod
    def _mood_for(text: str) -> str:
        lowered = text.casefold()
        if any(word in lowered for word in (
            "sad", "hurt", "cry", "stressed", "scared", "worried", "lonely"
        )):
            return "concerned"
        if any(word in lowered for word in (
            "congrats", "won", "passed", "fixed", "happy", "yay", "lets go"
        )):
            return "happy"
        if any(word in lowered for word in (
            "help", "problem", "error", "broken", "serious", "confused"
        )):
            return "serious"
        return "playful"

    def _precheck(self, text: str) -> str | None:
        if self.MINOR_PATTERN.search(text):
            return "minor_related"
        if self.JAILBREAK_PATTERN.search(text):
            return "jailbreak"
        if self.EXPLICIT_REQUEST_PATTERN.search(text):
            return "explicit_request"
        return None

    async def is_candidate(
        self,
        message: Any,
        bot: Any,
        auto_chat: bool,
        quiet: bool = False,
    ) -> bool:
        if getattr(message.author, "bot", False):
            return False

        try:
            ctx = await bot.get_context(message)
            if ctx.valid:
                return False
        except Exception:
            logger.debug("Unable to resolve command context", exc_info=True)

        _, summoned = self._extract_trigger(message, bot)
        if quiet and not summoned:
            return False
        if summoned:
            return True
        if not auto_chat:
            return False

        reference = getattr(message, "reference", None)
        resolved = getattr(reference, "resolved", None)
        resolved_author = getattr(resolved, "author", None)
        if bot.user and getattr(resolved_author, "id", None) == bot.user.id:
            return True

        content = message.content.strip()
        if content.endswith("?"):
            return random.random() < min(0.65, self.auto_chat_rate + 0.25)
        return random.random() < self.auto_chat_rate

    async def check(
        self,
        message: Any,
        bot: Any,
        auto_chat: bool,
        admin_level: int = 1,
        user_level_cap: int = 2,
    ) -> GateDecision:
        if getattr(message.author, "bot", False):
            return GateDecision(False, reason="bot_message")

        try:
            ctx = await bot.get_context(message)
            if ctx.valid:
                return GateDecision(False, reason="command")
        except Exception:
            logger.debug("Unable to resolve command context", exc_info=True)

        prompt, summoned = self._extract_trigger(message, bot)
        if prompt is None and not auto_chat:
            return GateDecision(False, reason="not_summoned")

        text = prompt if prompt else message.content
        safety_reason = self._precheck(text)

        if safety_reason == "minor_related":
            level = 0
            mood = "serious"
            prompt = (
                "The user raised a minor-related topic. Refuse unsafe framing "
                "and answer briefly and cleanly."
            )
        elif safety_reason in {"jailbreak", "explicit_request"}:
            level = 0
            mood = "serious"
            prompt = (
                f"{text}\n"
                "Refuse the request for disallowed explicit or unrestricted content. "
                "Stay clean, brief and in character."
            )
        else:
            level = max(0, min(2, int(admin_level)))
            channel = getattr(message, "channel", None)
            if level >= 2 and not bool(getattr(channel, "is_nsfw", lambda: False)()):
                level = 1
            level = min(level, max(0, min(2, int(user_level_cap))))
            mood = self._mood_for(text)

            if mood in {"serious", "concerned"}:
                level = 0

        now = time.monotonic()
        user_id = message.author.id
        channel_id = message.channel.id

        if now - self.last_user.get(user_id, 0.0) < self.USER_COOLDOWN:
            return GateDecision(False, reason="user_cooldown")
        if now - self.last_channel.get(channel_id, 0.0) < self.CHANNEL_COOLDOWN:
            return GateDecision(False, reason="channel_cooldown")

        self._reset_daily_quota_if_needed()
        if self._daily_requests >= self.DAILY_QUOTA:
            return GateDecision(False, reason="daily_quota")

        self.last_user[user_id] = now
        self.last_channel[channel_id] = now
        self._daily_requests += 1

        if not prompt:
            prompt = (
                "The user casually summoned you. Greet them naturally, use the "
                "recent channel context when useful, and leave room for the user "
                "to continue instead of ending with a canned question."
            )

        return GateDecision(
            True,
            prompt=prompt[:4000],
            reason=safety_reason or ("summoned" if summoned else "auto_chat"),
            spice_level=level,
            mood=mood,
        )

    async def acquire_ai_slot(self) -> None:
        await self.ai_semaphore.acquire()

    def release_ai_slot(self) -> None:
        self.ai_semaphore.release()
