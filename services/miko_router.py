from __future__ import annotations

import re


class MikoRouter:
    """Lightweight local hints; Groq performs the actual language understanding."""

    def classify(self, text: str) -> str:
        lowered = text.casefold()

        def contains(phrase):
            return re.search(r'(?<!\w)' + re.escape(phrase.strip()).replace(r'\ ', r'\s+') + r'(?!\w)', lowered) is not None

        if any(contains(phrase) for phrase in (
            "ban ", "kick ", "timeout ", "mute ", "lock ",
            "unlock ", "give role", "remove role", "set chat",
            "disable miko", "enable miko", "quiet mode",
        )):
            return "action"

        if any(contains(phrase) for phrase in (
            "latest", "today", "currently", "current", "recent",
            "news", "price", "pricing", "cost", "availability", "aaj ki khabar", "आज की खबर",
            "release date", "launch date", "search online", "look up",
            "on the internet", "on the web",
        )):
            return "search"

        if any(contains(word) for word in (
            "python", "javascript", "typescript", "java", "code",
            "coding", "error", "bug", "stack trace", "programming",
        )):
            return "coding"

        if any(contains(word) for word in (
            "genshin", "wuthering", "wuwa", "palworld",
            "minecraft", "valorant", "gaming", "game",
        )):
            return "gaming"

        if any(contains(word) for word in (
            "command", "commands", "what can you do", "how do i use",
            "which command", "what command", "help me use",
        )):
            return "help"

        if any(contains(word) for word in (
            "sad", "hurt", "cry", "stressed", "scared", "worried",
            "depressed", "upset", "anxious", "udaas", "pareshan", "परेशान", "उदास",
        )):
            return "serious"

        if any(contains(word) for word in (
            "bye", "goodnight", "good night", "gn", "leaving", "shubh ratri", "शुभ रात्रि",
        )):
            return "goodbye"

        return "chat"
