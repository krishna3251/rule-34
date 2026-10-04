from __future__ import annotations


class MikoRouter:
    """Lightweight local hints; Groq performs the actual language understanding."""

    def classify(self, text: str) -> str:
        lowered = text.casefold()

        if any(phrase in lowered for phrase in (
            "ban ", "kick ", "timeout ", "mute ", "lock ",
            "unlock ", "give role", "remove role", "set chat",
            "disable miko", "enable miko", "quiet mode",
        )):
            return "action"

        if any(phrase in lowered for phrase in (
            "latest", "today", "currently", "current", "recent",
            "news", "price", "pricing", "cost", "availability",
            "release date", "launch date", "search online", "look up",
            "on the internet", "on the web",
        )):
            return "search"

        if any(word in lowered for word in (
            "python", "javascript", "typescript", "java", "code",
            "coding", "error", "bug", "stack trace", "programming",
        )):
            return "coding"

        if any(word in lowered for word in (
            "genshin", "wuthering", "wuwa", "palworld",
            "minecraft", "valorant", "gaming", "game",
        )):
            return "gaming"

        if any(word in lowered for word in (
            "command", "commands", "what can you do", "how do i use",
            "which command", "what command", "help me use",
        )):
            return "help"

        if any(word in lowered for word in (
            "sad", "hurt", "cry", "stressed", "scared", "worried",
            "depressed", "upset", "anxious",
        )):
            return "serious"

        if any(word in lowered for word in (
            "bye", "goodnight", "good night", "gn", "leaving",
        )):
            return "goodbye"

        return "chat"
