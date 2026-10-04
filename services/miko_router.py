from __future__ import annotations


class MikoRouter:
    """Lightweight local intent routing. Never makes a second AI request."""

    def classify(self, text: str) -> str:
        lowered = text.casefold()

        if any(word in lowered for word in (
            "python", "javascript", "typescript", "java", "code",
            "coding", "error", "bug", "stack trace",
        )):
            return "coding"

        if any(word in lowered for word in (
            "genshin", "wuthering", "wuwa", "palworld",
            "minecraft", "valorant", "gaming", "game",
        )):
            return "gaming"

        if any(word in lowered for word in (
            "help", "how do i", "how can i", "why", "what is", "explain",
        )):
            return "question"

        if any(word in lowered for word in (
            "sad", "hurt", "cry", "stressed", "scared", "worried",
        )):
            return "serious"

        if any(word in lowered for word in (
            "bye", "goodnight", "good night", "gn", "leaving",
        )):
            return "goodbye"

        return "chat"
