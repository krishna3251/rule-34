from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from .models import ImageIntent


class MikoImageIntentDetector:
    """Deterministic first-stage image intent detector.

    This intentionally does not replace Miko's LLM. It provides a cheap,
    predictable signal for image selection and keeps the old 24-emotion engine
    as the final fallback when confidence is low.
    """

    EMOTION_RULES: dict[str, tuple[tuple[str, float], ...]] = {
        "happy": (("happy", .8), ("glad", .6), ("yay", .9), ("congrats", .9), ("passed", .7), ("success", .7), ("😊", .8), ("😄", .9)),
        "laughing": (("haha", .9), ("lmao", .9), ("lol", .7), ("funny", .7), ("😂", 1.0), ("🤣", 1.0)),
        "playful": (("tease", .9), ("teasing", .9), ("brat", .6), ("troublemaker", .7), ("cheeky", .8), ("hehe", .7)),
        "confused": (("confused", 1.0), ("huh", .6), ("what do you mean", .9), ("??", .5)),
        "surprised": (("really?", .7), ("seriously?", .6), ("no way", .8), ("unexpected", .8), ("😮", .9), ("😳", .8)),
        "shocked": (("wtf", .9), ("what the hell", .8), ("insane", .7), ("unbelievable", .8), ("shocked", 1.0)),
        "angry": (("angry", 1.0), ("furious", 1.0), ("shut up", .7), ("hate", .5), ("😡", 1.0)),
        "sad": (("sad", 1.0), ("hurt", .8), ("upset", .8), ("lonely", .8), ("miss you", .7), ("😢", 1.0)),
        "tired": (("tired", 1.0), ("exhausted", .9), ("drained", .8), ("sleepy", .9)),
        "nervous": (("nervous", 1.0), ("worried", .8), ("scared", .8), ("anxious", .8)),
        "embarrassed": (("embarrassed", 1.0), ("awkward", .8), ("oops", .6)),
        "affectionate": (("love", .7), ("adore", .8), ("darling", .6), ("❤️", .9), ("💕", .9), ("thanks", .3), ("thank you", .3)),
        "smug": (("obviously", .6), ("as if", .7), ("knew it", .8), ("predictable", .8), ("smug", 1.0)),
        "sleepy": (("goodnight", .9), ("good night", .9), ("gn", .8), ("bed", .6), ("drowsy", .9)),
    }

    ACTION_RULES: dict[str, tuple[str, ...]] = {
        "teasing": ("tease", "teasing", "brat", "troublemaker", "caught you", "you wish"),
        "comforting": ("tired", "sad", "hurt", "upset", "rough day", "bad day"),
        "celebrating": ("won", "passed", "success", "congrats", "congratulations"),
        "greeting": ("hello", "hi miko", "hey miko", "good morning", "good evening"),
        "goodbye": ("bye", "goodnight", "good night", "gn", "leaving"),
    }

    def analyze(
        self,
        user_text: str,
        reply_text: str = "",
        *,
        history: Iterable[dict[str, str]] = (),
        intent: str = "chat",
        mood: str = "playful",
        previous_emotion: str | None = None,
        previous_image_type: str | None = None,
    ) -> ImageIntent:
        user = str(user_text or "").casefold()
        reply = str(reply_text or "").casefold()
        recent = " ".join(str(item.get("content", "")) for item in list(history)[-4:]).casefold()
        text = f"{user} {reply} {recent}".strip()

        scores: dict[str, float] = defaultdict(float)
        for emotion, rules in self.EMOTION_RULES.items():
            for pattern, weight in rules:
                if pattern in text:
                    scores[emotion] += weight

        if mood == "happy":
            scores["happy"] += .5
        elif mood == "serious":
            scores["sad"] += .25
        elif mood == "playful":
            scores["playful"] += .25

        primary = max(scores, key=scores.get, default="neutral")
        primary_score = scores.get(primary, 0.0)
        if primary_score <= 0:
            primary = "neutral"

        secondary = None
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        for emotion, score in ranked[1:]:
            if score >= .65 and emotion != primary:
                secondary = emotion
                break

        action = self._detect_action(text)
        expression = self._expression(primary, action)

        # Technical/search/coding replies generally benefit from text only.
        image_type = self._image_type(intent, primary, action)
        needed = image_type != "none"

        # Avoid blindly repeating the same reaction category.
        if needed and previous_image_type == image_type and previous_emotion == primary:
            if secondary:
                primary, secondary = secondary, primary
                expression = self._expression(primary, action)
            else:
                needed = False
                image_type = "none"

        intensity = min(1.0, max(0.0, primary_score / 2.5))
        confidence = min(0.98, .45 + min(primary_score, 2.5) * .18)
        if action:
            confidence = min(.98, confidence + .08)
        if intent in {"coding", "search", "help"} and primary == "neutral":
            confidence = .9

        tags = tuple(dict.fromkeys(x for x in (primary, secondary, action, expression, image_type) if x))
        return ImageIntent(
            needed=needed,
            image_type=image_type,
            primary_emotion=primary,
            secondary_emotion=secondary,
            action=action,
            expression=expression,
            intensity=round(intensity, 3),
            confidence=round(confidence, 3),
            tags=tags,
            reason=f"{primary} / {action or 'no explicit action'}",
        )

    @staticmethod
    def _detect_action(text: str) -> str | None:
        for action, patterns in MikoImageIntentDetector.ACTION_RULES.items():
            if any(pattern in text for pattern in patterns):
                return action
        return None

    @staticmethod
    def _expression(emotion: str, action: str | None) -> str | None:
        if action == "teasing" or emotion == "playful":
            return "smirk"
        if emotion == "laughing":
            return "laughing"
        if emotion == "surprised":
            return "wide_eyes"
        if emotion == "shocked":
            return "shocked"
        if emotion == "sad":
            return "teary"
        if emotion in {"affectionate", "happy"}:
            return "warm_smile"
        if emotion == "angry":
            return "annoyed"
        return None

    @staticmethod
    def _image_type(intent: str, emotion: str, action: str | None) -> str:
        if intent in {"coding", "search", "help"} and emotion == "neutral":
            return "none"
        if action in {"greeting", "goodbye"}:
            return "reaction"
        if emotion == "neutral":
            return "none"
        if action == "comforting":
            return "support"
        if emotion in {"playful", "laughing", "confused", "surprised", "shocked", "smug"}:
            return "reaction"
        return "emotion"
