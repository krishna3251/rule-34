from __future__ import annotations

import re


class MikoRouter:
    """Identify common conversation needs before generation."""

    SUPPORT = re.compile(
        r"\b(?:i(?:'m| am| feel) (?:really |so |very )?(?:sad|hurt|scared|"
        r"stressed|worried|depressed|upset|anxious|lonely|overwhelmed|not (?:okay|ok|happy))|"
        r"(?:had a |having a |it(?:'s| was) a )(?:rough|bad|hard) day|"
        r"panic attack|i need to vent|i (?:lost|miss) my (?:dad|mom|father|mother)|"
        r"mood (?:bahut )?off|(?:bahut |bohot )?(?:udaas|pareshan)|"
        r"(?:bahut |bohot )?bura lag raha)\b|परेशान|उदास"
    )
    REPAIR = re.compile(
        r"\b(?:that(?:'s| is) not what i (?:meant|asked)|you (?:misunderstood|missed my point)|"
        r"i meant to say|mera matlab (?:tha|hai)|samjhi nahi)\b"
    )
    LIVE_REQUEST = re.compile(
        r"\b(?:search online|look up|on the internet|on the web|aaj ki khabar|"
        r"release date|launch date|latest news|latest version|breaking news)\b|आज की खबर"
    )
    FRESHNESS = re.compile(r"\b(?:latest|today|currently|current|recent|now|aaj)\b")
    LOOKUP = re.compile(
        r"\b(?:what|which|how much|when|where|who|tell me|show me|check|find|"
        r"batao|dikhao|kitna|kya)\b|\b(?:price|pricing|cost|availability) of\b|\?\s*$"
    )

    def classify(self, text: str) -> str:
        lowered = text.casefold().replace("’", "'")
        if self.SUPPORT.search(lowered):
            return "serious"
        if self.REPAIR.search(lowered):
            return "clarify"

        def contains(phrase):
            return re.search(r'(?<!\w)' + re.escape(phrase.strip()).replace(r'\ ', r'\s+') + r'(?!\w)', lowered) is not None

        if any(contains(phrase) for phrase in (
            "ban ", "kick ", "timeout ", "mute ", "lock ",
            "unlock ", "give role", "remove role", "set chat",
            "disable miko", "enable miko", "quiet mode",
        )):
            return "action"

        lookup_topic = any(contains(word) for word in ("news", "price", "pricing", "availability"))
        factual_topic = lookup_topic or any(contains(word) for word in (
            "release", "version", "weather", "update", "patch", "cost", "score", "near me", "status",
            "stock", "stocks", "market", "rate", "trading", "headlines", "what happened",
        ))
        if (self.LIVE_REQUEST.search(lowered)
                or (self.LOOKUP.search(lowered) and (
                    lookup_topic or (factual_topic and self.FRESHNESS.search(lowered))
                ))):
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
