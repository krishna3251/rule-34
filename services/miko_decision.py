from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.miko_gate import GateDecision
from services.miko_router import MikoRouter


@dataclass(slots=True)
class MikoDecision:
    """Application-level decision made before the LLM is called."""

    respond: bool
    prompt: str = ""
    intent: str = "chat"
    priority: str = "normal"
    response_mode: str = "chat"
    memory_scope: str = "user+channel"
    web_search: bool = False
    allow_actions: bool = False
    strict: bool = False
    spice_level: int = 0
    mood: str = "playful"
    reason: str = ""
    confidence: float = 0.0


class MikoDecisionEngine:
    """Rukiya-style decision layer for Miko.

    This layer decides whether and how Miko should respond. The LLM is
    responsible for language generation, not application control flow.
    """

    SAFETY_REASONS = frozenset({
        "minor_related",
        "jailbreak",
        "explicit_request",
        "safety_refusal",
    })

    def __init__(self, router: MikoRouter | None = None) -> None:
        self.router = router or MikoRouter()

    @staticmethod
    def _priority(reason: str, intent: str, auto_chat: bool) -> str:
        if reason in {"summoned", "direct"}:
            return "high"
        if reason in MikoDecisionEngine.SAFETY_REASONS:
            return "high"
        if intent in {"action", "search", "help", "serious"}:
            return "high"
        if auto_chat:
            return "low"
        return "normal"

    @staticmethod
    def _response_mode(reason: str, intent: str) -> str:
        if reason in MikoDecisionEngine.SAFETY_REASONS:
            return "safety_refusal"
        if intent == "action":
            return "action"
        if intent == "search":
            return "search"
        if intent == "help":
            return "help"
        if intent == "coding":
            return "technical"
        if intent == "gaming":
            return "gaming"
        if intent == "serious":
            return "support"
        if intent == "goodbye":
            return "goodbye"
        return "chat"

    @staticmethod
    def _confidence(prompt: str, intent: str) -> float:
        if not prompt.strip():
            return 0.25
        if intent in {"action", "search", "coding", "gaming", "help", "serious", "goodbye"}:
            return 0.90
        return 0.65

    def decide(
        self,
        gate: GateDecision,
        *,
        auto_chat: bool = False,
        profile: dict[str, Any] | None = None,
    ) -> MikoDecision:
        if not gate.respond:
            return MikoDecision(
                respond=False,
                reason=gate.reason,
                mood=gate.mood,
                spice_level=gate.spice_level,
            )

        prompt = str(gate.prompt or "").strip()[:4000]
        intent = self.router.classify(prompt)
        safety = gate.reason in self.SAFETY_REASONS

        response_mode = self._response_mode(gate.reason, intent)
        priority = self._priority(gate.reason, intent, auto_chat)

        user_cap = 2
        if profile is not None:
            try:
                user_cap = max(0, min(2, int(profile.get("level_cap", 2) or 2)))
            except (TypeError, ValueError):
                user_cap = 2

        level = max(0, min(2, gate.spice_level, user_cap))
        if safety:
            level = 0

        # Action tools are only useful when the user explicitly summoned Miko
        # for an action. Read-only tools remain available to answer questions.
        allow_actions = gate.reason in {"summoned", "direct"} and intent == "action"

        return MikoDecision(
            respond=True,
            prompt=prompt,
            intent=intent,
            priority=priority,
            response_mode=response_mode,
            memory_scope="user+channel",
            web_search=(intent == "search" and not safety),
            allow_actions=allow_actions,
            strict=safety,
            spice_level=level,
            mood=gate.mood,
            reason=gate.reason,
            confidence=self._confidence(prompt, intent),
        )
