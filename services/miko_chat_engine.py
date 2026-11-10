from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from services.miko_ai import MikoAI, allow_external, prefer_local, provider_usage
from collections import Counter
from services.brain.config import BrainConfig
from services.brain.runtime import BrainRuntime, worker
from services.brain.skills import LocalSkills
from services.brain.store import BrainStore
from services.access_policy import content_access
from services.miko_context import MikoContextBuilder
from services.miko_decision import MikoDecision, MikoDecisionEngine
from services.miko_emotion import MikoEmotionEngine
from services.miko_gate import GateDecision, MikoGate
from services.miko_memory import MikoMemory
from services.miko_profile import MikoProfile
from services.miko_personality import MikoPersonality
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


class MikoChatEngine:
    """Miko conversational application, independent of other bot repositories.

    Platform/cog code should only decide whether to call this engine and how
    to deliver the result. All conversation control lives here:
    gate -> decision -> memory -> context -> tools/AI -> validation -> memory.
    """

    def __init__(self, *, config: BrainConfig | None = None, memory: MikoMemory | None = None,
                 profile: MikoProfile | None = None, store: BrainStore | None = None) -> None:
        self.gate = MikoGate()
        self.memory = memory if memory is not None else MikoMemory(ephemeral=True)
        self.config = config or BrainConfig.from_env()
        self.runtime = BrainRuntime(self.config)
        self.skills = LocalSkills()
        self.store = store
        self.emotion = MikoEmotionEngine()
        self.profile = profile if profile is not None else MikoProfile()
        self.router = MikoRouter()
        self.decision = MikoDecisionEngine(self.router)
        self.context = MikoContextBuilder()
        self.ai = MikoAI(self.config)
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
        if self.store is None:
            self.store = BrainStore(cog.bot.db.db_path, self.config.retention_days)

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
        return f"{getattr(message.guild, 'id', 0) or 0}:{message.channel.id}:{message.author.id}"

    def _next_turn(self, key: str) -> int:
        value = self.turn_counts.get(key, 0) + 1
        self.turn_counts[key] = value
        while len(self.turn_counts) > 256:
            self.turn_counts.pop(next(iter(self.turn_counts)))
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
        if not allow_actions:
            tool_schemas = [s for s in tool_schemas if s["function"]["name"] != "run_bot_command"]

        for _ in range(self.max_tool_iterations):
            if sum(len(json.dumps(m, ensure_ascii=False)) for m in working) > self.config.context_chars:
                return "This task exceeded the context limit. Please split it into smaller requests.", used_web
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
            return max(0, min(2, int(profile.get("level_cap", 2))))
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
        return MikoPersonality.wants_clean_chat(prompt)

    def _apply_local_request(self, message: Any, decision: MikoDecision) -> MikoDecision:
        if self._is_stop_flirting_request(decision.prompt):
            self.profile.disable_flirting(message.author.id)
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
        self._ensure_runtime()
        messages = await worker(self.context.build,
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
            memory_scope="user+guild+channel",
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
            reply = "I lost my train of thought. Try that again?"

        if profile.get('catalog_allowed') and not await content_access(bot, message.guild, message.channel, message.author):
            return MikoResult(True, 'Your access changed while I was answering. Use a verified, age-restricted channel for catalog or channel-knowledge requests.', reason='access_revoked')
        current_profile = await self.conversation_profile(message, bot)
        if decision.spice_level > 0 and self._profile_level(current_profile) == 0:
            # An opt-out or access revocation during generation wins over that
            # in-flight turn. Regenerate with the existing clean retry path.
            decision.spice_level = 0
            decision.mood = "neutral"
            valid, cleaned, validation_reason = False, "", "tone_changed"
        else:
            valid, cleaned, validation_reason = self.response.validate(
                self._scope(message),
                reply,
                decision.spice_level,
            )

        if not valid:
            retry_messages = await worker(self.context.build,
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
                memory_scope="user+guild+channel",
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
                self._scope(message),
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

        await self._record(message, decision.prompt, cleaned, display_name)

        emotion = self.emotion.analyze(
            decision.prompt,
            cleaned,
            mood=decision.mood,
            intent=decision.intent,
            spice_level=decision.spice_level,
        )

        return MikoResult(
            replied=True,
            text=cleaned,
            reason=decision.reason,
            emotion_id=emotion.id,
            emotion=emotion.name,
            intent=decision.intent,
            response_mode=decision.response_mode,
            priority=decision.priority,
            web_search_used=used_web,
        )

    async def conversation_profile(self, message: Any, bot: Any) -> dict[str, Any]:
        guild = getattr(message, "guild", None)
        profile = await worker(self.profile.get, message.author.id, getattr(guild, "id", None), message.channel.id)
        # Consent and current access are checked on every turn, including /askmiko.
        if (profile.get("flirt_opt_in") is not True or self._profile_level(profile) == 0
                or not await content_access(bot, guild, message.channel, message.author)):
            profile["level_cap"] = 0
        return profile

    async def _run_gate(
        self,
        message: Any,
        bot: Any,
        *,
        auto_chat: bool,
        admin_level: int,
    ) -> tuple[GateDecision, dict[str, Any]]:
        profile = await self.conversation_profile(message, bot)
        user_level_cap = self._profile_level(profile)
        gate = await self.gate.check(
            message,
            bot,
            auto_chat=auto_chat,
            admin_level=admin_level,
            user_level_cap=user_level_cap,
        )
        return gate, profile

    async def handle(self, message: Any, bot: Any, *, auto_chat=False, admin_level=1, disabled=False, quiet=False) -> MikoResult:
        return await self._entry(lambda: self._handle(message, bot, auto_chat=auto_chat,
            admin_level=admin_level, disabled=disabled, quiet=quiet), message, getattr(message, 'id', None))

    async def ask_direct(self, message: Any, bot: Any, *, prompt: str, admin_level=1) -> MikoResult:
        if len(prompt) > 4000:
            return MikoResult(True, 'Please keep each request within 4,000 characters.', reason='too_large')
        return await self._entry(lambda: self._ask_direct(message, bot, prompt=prompt,
            admin_level=admin_level), message, getattr(message, 'id', None))

    async def _handle(
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

        if len(getattr(message, "content", "")) > 4200:
            return MikoResult(False, reason="too_large")
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

        return await self._process_gate(message, bot, gate, profile, auto_chat=auto_chat)

    async def _ask_direct(
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

        profile = await self.conversation_profile(message, bot)
        gate = await self.gate.check_direct(
            message,
            prompt,
            admin_level=admin_level,
            user_level_cap=self._profile_level(profile),
        )
        if not gate.respond:
            return MikoResult(False, reason=gate.reason)

        return await self._process_gate(message, bot, gate, profile)

    async def _process_gate(self, message, bot, gate, profile, *, auto_chat=False):
        if self._is_forget_request(gate.prompt):
            await self.clear_user(message.author.id)
            return MikoResult(True, 'Your stored Miko memory and profile have been deleted.', reason='forget_request')
        decision = self.decision.decide(gate, auto_chat=auto_chat, profile=profile)
        decision = await worker(self._apply_local_request, message, decision)
        scope = self._scope(message)
        prefs = await worker(self.store.preferences, scope) if self.store else {'history': False, 'external': True, 'style': 'adaptive'}
        # Exact calculations and explicit retrieval never spend provider quota.
        local = self.skills.calculate(decision.prompt) if not decision.strict else None
        if local is None and not decision.strict and decision.prompt.casefold().strip() == 'my id':
            local = str(message.author.id)
        if local is None and not decision.strict and decision.prompt.casefold().strip() in {'server info', 'channel info', 'list commands'}:
            from services.miko_tools import MikoToolContext
            tool = {'server info': 'get_server_overview', 'channel info': 'get_channel_info', 'list commands': 'list_bot_commands'}[decision.prompt.casefold().strip()]
            result = await self.tools.execute(tool, {}, MikoToolContext(bot, message, self.miko_chat, False))
            local = json.dumps(result, ensure_ascii=False, default=str)
        if local is None and not decision.strict and decision.prompt.casefold().startswith('memory:') and self.store:
            facts = await worker(self.store.facts, scope, decision.prompt[7:])
            local = '\n'.join(f"{f['key']}: {f['value']}" for f in facts) or 'No matching approved memory in this conversation.'
        if local is None and not decision.strict and decision.prompt.casefold().startswith('knowledge:') and self.store:
            if not await content_access(bot, message.guild, message.channel, message.author):
                local = 'Knowledge lookup requires this server’s verification and an age-restricted channel.'
            else:
                facts = await worker(self.store.facts, (*scope[:2], 0), decision.prompt[10:])
                local = '\n'.join(f"{f['key']}: {f['value']}" for f in facts) or 'No matching curated entry in this channel.'
        if local is not None:
            self.runtime.metrics['deterministic'] += 1
            await self._record(message, decision.prompt, local, getattr(message.author, 'display_name', 'User'))
            return MikoResult(True, local[:1800], reason='local_skill', intent='local', response_mode='help')
        if decision.web_search and not self.ai.web_search_available:
            return MikoResult(True, 'Live lookup is unavailable. I can help with general information, but cannot verify current facts for this request.', reason='search_unavailable')
        history = await worker(self.memory.history, message.author.id, scope[0], scope[1], private=True)
        if not history and prefs['history'] and self.store:
            history = await worker(self.store.history, scope)
        profile = await worker(self.profile.get, message.author.id, scope[0], scope[1])
        profile['style'] = prefs['style']
        if self.store:
            profile['approved_memories'] = await worker(self.store.personal_context, scope, decision.prompt)
            if await content_access(bot, message.guild, message.channel, message.author):
                profile['knowledge'] = await worker(self.store.facts, (*scope[:2], 0), decision.prompt, 3)
        profile['catalog_allowed'] = await content_access(bot, message.guild, message.channel, message.author)
        token = allow_external.set(bool(prefs['external']))
        local_token = prefer_local.set(not prefs['external'] or not self.config.external_enabled or (not decision.allow_actions and len(decision.prompt) <= 1000 and decision.intent not in {'coding', 'action'}))
        try:
            return await self._generate_and_finalize(
                message=message, bot=bot, decision=decision, history=history, profile=profile,
                display_name=getattr(message.author, 'display_name', None) or getattr(message.author, 'name', 'User'),
                conversation_key=self._conversation_key(message), turn=self._next_turn(self._conversation_key(message)))
        finally:
            allow_external.reset(token)
            prefer_local.reset(local_token)

    @staticmethod
    def _scope(message):
        return (getattr(message.guild, 'id', 0) or 0, message.channel.id, message.author.id)

    def _ensure_runtime(self):
        # Preserve compatibility for legacy adapters constructing the engine in tests.
        if not hasattr(self, 'config'):
            self.config = BrainConfig.from_env()
            self.runtime = BrainRuntime(self.config)
            self.skills = LocalSkills()
            self.store = None

    async def _record(self, message, prompt, reply, display_name):
        scope = self._scope(message)
        turns = [{'role': 'user', 'content': prompt[:1200], 'name': display_name[:80]},
                 {'role': 'assistant', 'content': reply[:1200], 'name': 'Miko'}]
        for turn in turns:
            await worker(self.memory.add, message.author.id, turn['role'], turn['content'],
                         scope[0], scope[1], name=turn['name'], private=True)
        if getattr(self, 'store', None):
            prefs = await worker(self.store.preferences, scope)
            if prefs['history']:
                await worker(self.store.append, scope, turns)

    async def _entry(self, operation, message, event_id):
        self._ensure_runtime()
        usage = Counter()
        token = provider_usage.set(usage)
        before = self.runtime.metrics['requests']
        try:
            return await self.runtime.run(self._scope(message), event_id, operation,
                lambda reason: MikoResult(False, reason=reason))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.runtime.metrics['errors'] += 1
            logger.warning('Miko request failed: %s', type(exc).__name__)
            return MikoResult(True, 'I could not complete that request. Please try again.', reason='component_failure')
        finally:
            provider_usage.reset(token)
            # Rejected events never ran operation. Admission itself is synchronous.
            if self.runtime.metrics['requests'] > before:
                scope = self._scope(message)
                stats = self.runtime.conversations.setdefault(scope, Counter())
                stats['requests'] += 1
                stats['external_calls'] += usage['openrouter'] + usage['groq']
                stats['local_calls'] += usage['local']
                self.runtime.conversations.move_to_end(scope)
                while len(self.runtime.conversations) > 256:
                    self.runtime.conversations.popitem(last=False)

    async def cancel(self, user_id, guild_id=None, channel_id=None):
        self._ensure_runtime()
        return await self.runtime.cancel(user_id, guild_id, channel_id)

    async def clear_user(self, user_id):
        await self.mutate_user(user_id, self.forget_user, user_id)

    async def mutate_user(self, user_id, function, *args, **kwargs):
        self._ensure_runtime()
        self.runtime.blocked_users[user_id] += 1
        try:
            await self.cancel(user_id)
            return await worker(function, *args, **kwargs)
        finally:
            self.runtime.blocked_users[user_id] -= 1
            if not self.runtime.blocked_users[user_id]:
                self.runtime.blocked_users.pop(user_id, None)

    async def reset_context(self, user_id, guild_id, channel_id):
        await self.mutate_user(user_id, self.reset_memory, user_id, guild_id, channel_id)

    async def update_preferences(self, scope, *, history=None, external=None, style=None):
        def update():
            result = self.store.set_preferences(scope, history=history, external=external, style=style)
            if history is False:
                self.memory.reset(scope[2], scope[0], scope[1])
            return result
        return await self.mutate_user(scope[2], update)

    async def forget_scope(self, scope, key=None):
        def update():
            self.store.forget(scope, key)
            self.memory.reset(scope[2], scope[0], scope[1])
            self.response.previous.pop(scope, None)
        await self.mutate_user(scope[2], update)

    async def close(self):
        await self.runtime.close()
        await self.ai.close()

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
        if self.store:
            self.store.clear_history((guild_id or 0, channel_id, user_id))
        self.response.previous.pop((guild_id or 0, channel_id, user_id), None)

    def forget_user(self, user_id: int) -> None:
        self.memory.reset(user_id)
        self.profile.forget(user_id)
        if getattr(self, "store", None):
            self.store.forget_user(user_id)
        if self.memory.ephemeral and self.memory.path.exists():
            MikoMemory(self.memory.path).reset(user_id)
        for key in list(self.runtime.conversations):
            if key[2] == user_id:
                self.runtime.conversations.pop(key, None)
        self.response.previous = {k:v for k,v in self.response.previous.items() if not (isinstance(k, tuple) and k[2] == user_id) and k != user_id}
