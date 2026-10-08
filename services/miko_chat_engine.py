from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from services.miko_ai import MikoAI
from services.miko_image import MikoImageIntentDetector
from services.miko_image.history import MikoImageHistory
from services.miko_context import MikoContextBuilder
from services.miko_decision import MikoDecision, MikoDecisionEngine
from services.miko_emotion import MikoEmotionEngine
from services.miko_gate import GateDecision, MikoGate
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
    emotion_id: int = 1
    emotion: str = "Neutral"
    intent: str = "chat"
    response_mode: str = "chat"
    priority: str = "normal"
    web_search_used: bool = False
    image_needed: bool = True
    image_type: str = "emotion"
    image_confidence: float = 0.0


class MikoChatEngine:
    """Single Rukiya-style conversational engine for Miko.

    Platform/cog code should only decide whether to call this engine and how
    to deliver the result. All conversation control lives here:
    gate -> decision -> memory -> context -> tools/AI -> validation -> memory.
    """

    def __init__(self) -> None:
        self.gate = MikoGate()
        self.memory = MikoMemory()
        self.emotion = MikoEmotionEngine()
        self.image_detector = MikoImageIntentDetector()
        self.image_history = MikoImageHistory()
        self.profile = MikoProfile()
        self.router = MikoRouter()
        self.decision = MikoDecisionEngine(self.router)
        self.context = MikoContextBuilder()
        self.ai = MikoAI()
        self.response = MikoResponseProcessor()
        self.tools = MikoToolRegistry()
        self.miko_chat: Any | None = None
        self.turn_counts: dict[str, int] = {}

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

    def _conversation_key(self, message: Any) -> str:
        return f"{getattr(message.guild, 'id', 0) or 0}:{message.channel.id}"

    def _next_turn(self, key: str) -> int:
        value = self.turn_counts.get(key, 0) + 1
        self.turn_counts[key] = value
        return value

    def _adaptive_temperature(self, intent: str, mood: str, turn: int) -> float:
        return self.context.social.temperature(intent, mood, turn)

    async def _agent_generate(
        self,
        messages: list[dict[str, Any]],
        *,
        bot: Any,
        message: Any,
        allow_actions: bool,
        strict: bool = False,
        allow_web: bool = True,
        temperature: float | None = None,
    ) -> tuple[str, bool]:
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
                temperature=temperature,
            )
            used_web = used_web or ai_result.web_search_used

            if not ai_result.tool_calls:
                return ai_result.text.strip(), used_web

            if total_tool_calls + len(ai_result.tool_calls) > self.max_tool_calls:
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
                working.append({
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or ""),
                    "name": name,
                    "content": json.dumps(
                        result,
                        ensure_ascii=False,
                        default=str,
                    )[:5000],
                })

        return (
            "I stopped after reaching the automation limit for this request.",
            used_web,
        )

    @staticmethod
    def _profile_level(profile: dict[str, Any]) -> int:
        try:
            return max(0, min(2, int(profile.get("level_cap", 2) or 2)))
        except (TypeError, ValueError):
            return 2

    @staticmethod
    def _is_forget_request(prompt: str) -> bool:
        return prompt.casefold().strip() in {
            "forget me",
            "forget my memory",
            "delete my memory",
            "delete my data",
        }

    @staticmethod
    def _is_stop_flirting_request(prompt: str) -> bool:
        return prompt.casefold().strip() in {
            "stop flirting",
            "stop flirting please",
            "don't flirt",
            "dont flirt",
        }

    def _apply_local_request(self, message: Any, decision: MikoDecision) -> MikoDecision:
        if self._is_stop_flirting_request(decision.prompt):
            self.profile.set(message.author.id, "level_cap", 0)
            decision.spice_level = 0
            decision.mood = "serious"
            decision.response_mode = "support"
        return decision

    async def _generate_and_finalize(
        self,
        *,
        message: Any,
        bot: Any,
        decision: MikoDecision,
        history: list[dict[str, str]],
        profile: dict[str, Any],
        display_name: str,
        conversation_key: str,
        turn: int,
    ) -> MikoResult:
        self.memory.add(
            message.author.id,
            "user",
            decision.prompt,
            getattr(message.guild, "id", None),
            message.channel.id,
            name=display_name,
        )

        messages = self.context.build(
            prompt=decision.prompt,
            history=history,
            spice_level=decision.spice_level,
            mood=decision.mood,
            intent=decision.intent,
            profile=profile,
            strict=decision.strict,
            user_name=display_name,
            conversation_key=conversation_key,
            turn_count=turn,
            priority=decision.priority,
            response_mode=decision.response_mode,
            memory_scope=decision.memory_scope,
            web_search=decision.web_search,
        )

        temperature = self._adaptive_temperature(
            decision.intent,
            decision.mood,
            turn,
        )

        await self.gate.acquire_ai_slot()
        try:
            reply, used_web = await self._agent_generate(
                messages,
                bot=bot,
                message=message,
                allow_actions=decision.allow_actions,
                strict=decision.strict,
                allow_web=decision.web_search,
                temperature=temperature,
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
            retry_messages = self.context.build(
                prompt=decision.prompt,
                history=history,
                spice_level=0,
                mood="serious",
                intent=decision.intent,
                profile=profile,
                strict=True,
                user_name=display_name,
                conversation_key=conversation_key,
                turn_count=turn,
                priority="high",
                response_mode="safety_refusal",
                memory_scope=decision.memory_scope,
                web_search=False,
            )
            await self.gate.acquire_ai_slot()
            try:
                retry, _ = await self._agent_generate(
                    retry_messages,
                    bot=bot,
                    message=message,
                    allow_actions=False,
                    strict=True,
                    allow_web=False,
                    temperature=0.50,
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
            name="Miko",
        )

        previous = self.image_history.last(conversation_key)
        image_intent = self.image_detector.analyze(
            decision.prompt,
            cleaned,
            history=history,
            intent=decision.intent,
            mood=decision.mood,
            previous_emotion=previous.emotion if previous else None,
            previous_image_type=previous.image_type if previous else None,
        )

        # Map the richer detector back onto the existing 24-image catalog.
        # This keeps deployment safe while the semantic image index is built.
        emotion_ids = {
            "neutral": 1, "happy": 3, "laughing": 5, "playful": 6,
            "confused": 9, "surprised": 10, "shocked": 11, "angry": 12,
            "embarrassed": 13, "sad": 15, "tired": 16, "nervous": 19,
            "affectionate": 22, "smug": 23, "sleepy": 24,
        }
        emotion_id = emotion_ids.get(image_intent.primary_emotion, 1)
        legacy_emotion = self.emotion.analyze(
            decision.prompt,
            cleaned,
            mood=decision.mood,
            intent=decision.intent,
            spice_level=decision.spice_level,
        )
        if image_intent.confidence < 0.60:
            emotion_id = legacy_emotion.id

        if image_intent.needed:
            self.image_history.add(
                conversation_key,
                f"miko_{emotion_id:02d}",
                image_intent.image_type,
                image_intent.primary_emotion,
            )

        return MikoResult(
            replied=True,
            text=cleaned,
            reason=decision.reason,
            emotion_id=emotion_id,
            emotion=image_intent.primary_emotion.title(),
            intent=decision.intent,
            response_mode=decision.response_mode,
            priority=decision.priority,
            web_search_used=used_web,
            image_needed=image_intent.needed,
            image_type=image_intent.image_type,
            image_confidence=image_intent.confidence,
        )

    async def _run_gate(
        self,
        message: Any,
        bot: Any,
        *,
        auto_chat: bool,
        admin_level: int,
    ) -> tuple[GateDecision, dict[str, Any]]:
        profile = self.profile.get(message.author.id)
        user_level_cap = self._profile_level(profile)
        gate = await self.gate.check(
            message,
            bot,
            auto_chat=auto_chat,
            admin_level=admin_level,
            user_level_cap=user_level_cap,
        )
        return gate, profile

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
        """Process a Discord conversation event through the complete engine."""

        if disabled:
            return MikoResult(False, reason="channel_disabled")

        if quiet:
            trigger = self.gate._extract_trigger(message, bot)
            if trigger[1] is False:
                return MikoResult(False, reason="quiet_mode")

        gate, profile = await self._run_gate(
            message,
            bot,
            auto_chat=auto_chat,
            admin_level=admin_level,
        )
        if not gate.respond:
            return MikoResult(False, reason=gate.reason)

        if self._is_forget_request(gate.prompt):
            self.forget_user(message.author.id)
            return MikoResult(
                True,
                "Your stored Miko memory and profile have been deleted.",
                reason="forget_request",
            )

        decision = self.decision.decide(
            gate,
            auto_chat=auto_chat,
            profile=profile,
        )
        decision = self._apply_local_request(message, decision)

        guild_id = getattr(message.guild, "id", None)
        conversation_key = self._conversation_key(message)
        turn = self._next_turn(conversation_key)
        display_name = (
            getattr(message.author, "display_name", None)
            or getattr(message.author, "name", None)
            or "User"
        )

        history = self.memory.history(
            message.author.id,
            guild_id,
            message.channel.id,
        )
        return await self._generate_and_finalize(
            message=message,
            bot=bot,
            decision=decision,
            history=history,
            profile=self.profile.get(message.author.id),
            display_name=display_name,
            conversation_key=conversation_key,
            turn=turn,
        )

    async def ask_direct(
        self,
        message: Any,
        bot: Any,
        *,
        prompt: str,
        admin_level: int = 1,
    ) -> MikoResult:
        """Run a direct question through the same chat engine.

        This is used by /askmiko and ~askmiko so direct questions do not
        bypass the normal safety, memory, decision, tool and validation layers.
        """

        profile = self.profile.get(message.author.id)
        gate = await self.gate.check_direct(
            message,
            prompt,
            admin_level=admin_level,
            user_level_cap=self._profile_level(profile),
        )
        if not gate.respond:
            return MikoResult(False, reason=gate.reason)

        if self._is_forget_request(gate.prompt):
            self.forget_user(message.author.id)
            return MikoResult(
                True,
                "Your stored Miko memory and profile have been deleted.",
                reason="forget_request",
            )

        decision = self.decision.decide(
            gate,
            auto_chat=False,
            profile=profile,
        )
        decision = self._apply_local_request(message, decision)

        guild_id = getattr(message.guild, "id", None)
        conversation_key = self._conversation_key(message)
        turn = self._next_turn(conversation_key)
        display_name = (
            getattr(message.author, "display_name", None)
            or getattr(message.author, "name", None)
            or "User"
        )
        history = self.memory.history(
            message.author.id,
            guild_id,
            message.channel.id,
        )

        return await self._generate_and_finalize(
            message=message,
            bot=bot,
            decision=decision,
            history=history,
            profile=self.profile.get(message.author.id),
            display_name=display_name,
            conversation_key=conversation_key,
            turn=turn,
        )

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
