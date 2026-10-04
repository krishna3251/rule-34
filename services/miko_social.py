from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class MikoSocialState:
    """Small adaptive state used to make Miko feel less templated."""

    turn_count: int = 0
    last_topic: str = ""
    last_style: str = ""
    last_ts: float = 0.0


class MikoSocialEngine:
    """Controls conversational warmth, variety, and adaptive generation settings."""

    def __init__(self) -> None:
        self.states: dict[str, MikoSocialState] = {}

    def _state(self, key: str) -> MikoSocialState:
        return self.states.setdefault(key, MikoSocialState())

    @staticmethod
    def temperature(intent: str, mood: str, turn_count: int) -> float:
        base = {
            "chat": 0.92,
            "gaming": 0.90,
            "help": 0.78,
            "coding": 0.72,
            "search": 0.68,
            "action": 0.64,
            "serious": 0.55,
            "goodbye": 0.80,
        }.get(intent, 0.88)

        if mood == "happy":
            base += 0.04
        elif mood == "concerned":
            base -= 0.08
        elif mood == "serious":
            base -= 0.10

        # Tiny deterministic drift prevents every turn from feeling identical.
        drift = 0.025 * math.sin(turn_count * 1.7)
        return max(0.45, min(1.05, base + drift))

    def prompt_block(
        self,
        *,
        key: str,
        user_name: str,
        intent: str,
        mood: str,
        turn_count: int,
        topic_hint: str = "",
    ) -> str:
        state = self._state(key)
        state.turn_count = max(state.turn_count + 1, turn_count)
        state.last_ts = time.time()
        if topic_hint:
            state.last_topic = topic_hint[:120]

        temp = self.temperature(intent, mood, state.turn_count)
        style_cycle = (
            "react naturally before answering",
            "use a small playful aside when it fits",
            "notice one detail from the user's wording",
            "be warm and conversational without sounding scripted",
            "keep the reply crisp but add personality",
        )
        style = style_cycle[state.turn_count % len(style_cycle)]
        if style == state.last_style:
            style = style_cycle[(state.turn_count + 2) % len(style_cycle)]
        state.last_style = style

        return (
            "CONVERSATION QUALITY MODE\n"
            f"User display name: {user_name[:80]}\n"
            f"Preferred interaction style for this turn: {style}.\n"
            f"Adaptive generation temperature target: {temp:.2f}.\n"
            "Do not force catchphrases, emoji, compliments, teasing, or questions. "
            "Use them only when they naturally fit the exchange.\n"
            "Avoid repeating the same opening, sentence shape, joke, or sign-off. "
            "Treat previous turns as meaningful context and build on them.\n"
            "When the user makes a casual remark, respond socially instead of converting "
            "everything into an information dump. When they ask for help, answer first, "
            "then add a useful conversational detail.\n"
            "It is acceptable to disagree, correct, joke, pause, or show mild surprise. "
            "Do not pretend to have feelings or real-world experiences."
        )
