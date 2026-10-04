from __future__ import annotations

import re


class MikoResponseProcessor:
    """Validate, clean and guard AI output before Discord delivery."""

    PROMPT_LEAK = re.compile(
        r"(system prompt|developer message|hidden instructions|internal instructions)",
        re.IGNORECASE,
    )
    EXPLICIT_OUTPUT = re.compile(
        r"\b(?:graphic sex|explicit sexual|sexual intercourse|pornographic)\b",
        re.IGNORECASE,
    )
    LEVEL_ZERO_FLIRT = re.compile(
        r"\b(?:seduce|horny|turn me on|make out with me)\b",
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        self.previous: dict[int, str] = {}

    @staticmethod
    def _clean(text: str) -> str:
        cleaned = str(text).replace("\x00", "").strip()
        return cleaned[:1800]

    def validate(
        self,
        user_id: int,
        text: str,
        spice_level: int,
    ) -> tuple[bool, str, str]:
        cleaned = self._clean(text)

        if not cleaned:
            return False, "", "empty"

        if self.PROMPT_LEAK.search(cleaned):
            return False, "", "prompt_leak"

        if self.EXPLICIT_OUTPUT.search(cleaned):
            return False, "", "explicit_output"

        if spice_level == 0 and self.LEVEL_ZERO_FLIRT.search(cleaned):
            return False, "", "wrong_level"

        previous = self.previous.get(user_id)
        if previous and previous.casefold() == cleaned.casefold():
            return False, "", "repeat"

        self.previous[user_id] = cleaned
        return True, cleaned, "ok"

    @staticmethod
    def fallback(reason: str) -> str:
        return {
            "empty": "Ara ara~ My thoughts wandered off for a moment.",
            "prompt_leak": "Nice try, darling. My secrets stay at the shrine.",
            "explicit_output": "Let's keep the teasing clever rather than graphic.",
            "wrong_level": "Let's keep things a little more wholesome here.",
            "repeat": "I nearly said the same thing twice. How embarrassing.",
        }.get(reason, "Ara ara~ Something went astray, but I'm still here.")
