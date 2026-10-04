from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("discord_bot")


@dataclass(frozen=True, slots=True)
class MikoEmotion:
    """Numeric Miko reaction mapped to one of the 24 canonical images."""

    id: int
    name: str
    reason: str = ""


class MikoEmotionEngine:
    """Select and resolve one of Miko's 24 canonical reactions."""

    EMOTIONS: dict[int, str] = {
        1: "Neutral",
        2: "Smile",
        3: "Happy",
        4: "Wink",
        5: "Laugh",
        6: "Tease",
        7: "Thinking",
        8: "Curious",
        9: "Confused",
        10: "Surprised",
        11: "Shocked",
        12: "Angry",
        13: "Embarrassed",
        14: "Flustered",
        15: "Sad",
        16: "Tired",
        17: "Annoyed",
        18: "Pout",
        19: "Nervous",
        20: "Crying",
        21: "Blushing Happy",
        22: "Love",
        23: "Smug",
        24: "Sleepy",
    }

    PATTERNS: dict[int, tuple[tuple[str, int], ...]] = {
        3: (
            ("congrats", 4), ("congratulations", 4), ("won", 3),
            ("passed", 3), ("yay", 3), ("happy", 3), ("great news", 4),
            ("success", 3),
        ),
        5: (
            ("haha", 4), ("lmao", 4), ("lol", 3), ("funny", 2),
            ("laugh", 3), ("😂", 5), ("🤣", 5),
        ),
        6: (
            ("tease", 5), ("teasing", 5), ("troublemaker", 3),
            ("brat", 3), ("caught you", 3), ("you wish", 3),
        ),
        7: (
            ("think", 4), ("thinking", 4), ("hmm", 3), ("let me see", 3),
            ("consider", 3), ("why would", 2),
        ),
        8: (
            ("curious", 5), ("how", 1), ("why", 1), ("what happened", 3),
            ("tell me", 2), ("interesting", 3),
        ),
        9: (
            ("confused", 5), ("don't understand", 5),
            ("what do you mean", 4), ("huh", 3), ("??", 2),
        ),
        10: (
            ("surprised", 5), ("unexpected", 4), ("really?", 3),
            ("seriously?", 2), ("no way", 3), ("oh", 1),
        ),
        11: (
            ("shocked", 6), ("wtf", 5), ("what the hell", 4),
            ("insane", 3), ("unbelievable", 4),
        ),
        12: (
            ("angry", 6), ("furious", 6), ("hate", 3),
            ("idiot", 2), ("shut up", 3),
        ),
        13: (
            ("embarrassed", 6), ("awkward", 4), ("oops", 3), ("sorry", 1),
        ),
        14: (
            ("flustered", 6), ("blush", 5), ("blushing", 5),
            ("you made me", 2), ("stop looking", 3),
        ),
        15: (
            ("sad", 6), ("hurt", 5), ("disappointed", 5),
            ("upset", 4), ("lonely", 4), ("miss you", 3),
        ),
        16: (
            ("tired", 6), ("exhausted", 5), ("drained", 4), ("sleep", 2),
        ),
        17: (
            ("annoyed", 6), ("irritating", 5), ("annoying", 4),
            ("ugh", 4), ("seriously", 1),
        ),
        18: (
            ("pout", 6), ("hmph", 5), ("not fair", 4), ("meanie", 3),
        ),
        19: (
            ("nervous", 6), ("worried", 5), ("scared", 5),
            ("anxious", 5), ("uncertain", 3),
        ),
        20: (
            ("crying", 7), ("cried", 5), ("tears", 5),
            ("sob", 5), ("😭", 6), ("😢", 5),
        ),
        21: (
            ("blushing", 4), ("cute", 3), ("sweet", 2),
            ("love you", 5), ("❤️", 4), ("♥", 3),
        ),
        22: (
            ("love", 5), ("adore", 5), ("dear", 2), ("darling", 2),
            ("affection", 4), ("❤️", 3), ("💕", 4),
        ),
        23: (
            ("obviously", 3), ("as if", 3), ("of course", 2),
            ("knew it", 4), ("you're predictable", 4), ("smug", 6),
        ),
        24: (
            ("sleepy", 6), ("goodnight", 5), ("good night", 5),
            ("gn", 4), ("bed", 2), ("drowsy", 5),
        ),
        2: (
            ("smile", 4), ("glad", 3), ("nice", 2), ("thanks", 2),
            ("thank you", 2), ("😊", 4), ("🙂", 3),
        ),
        4: (
            ("wink", 6), ("😉", 5), ("hehe", 2), ("cheeky", 3),
        ),
    }

    SERIOUS_MOOD_DEFAULTS = {
        "concerned": 15,
        "serious": 7,
    }

    def __init__(
        self,
        image_dir: str | Path = "assets/miko/emotions",
        cache_dir: str | Path = "data/miko_emotion_cache",
    ) -> None:
        self.image_dir = Path(image_dir)
        self.cache_dir = Path(cache_dir)
        self.zip_candidates = (
            Path("Yae Miko Discord reaction images.zip"),
            Path("data") / "Yae Miko Discord reaction images.zip",
            self.image_dir / "Yae Miko Discord reaction images.zip",
        )

    def analyze(
        self,
        user_text: str,
        reply_text: str,
        *,
        mood: str = "playful",
        intent: str = "chat",
        spice_level: int = 1,
    ) -> MikoEmotion:
        user = str(user_text or "").casefold()
        reply = str(reply_text or "").casefold()
        combined = f"{user}\n{reply}"
        scores: dict[int, int] = {emotion_id: 0 for emotion_id in self.EMOTIONS}

        for emotion_id, patterns in self.PATTERNS.items():
            for pattern, weight in patterns:
                if pattern.casefold() in combined:
                    scores[emotion_id] += weight

        if intent == "goodbye":
            scores[24] += 8
        elif intent == "serious":
            scores[15] += 5
        elif intent == "help":
            scores[7] += 3
        elif intent == "search":
            scores[8] += 2
        elif intent == "gaming":
            scores[3] += 1

        if mood in self.SERIOUS_MOOD_DEFAULTS:
            scores[self.SERIOUS_MOOD_DEFAULTS[mood]] += 4
        elif mood == "happy":
            scores[3] += 4
            scores[2] += 2
        elif mood == "playful":
            scores[23] += 1

        if spice_level == 2 and scores[6] > 0:
            scores[6] += 1

        best_id = max(scores, key=scores.get)
        best_score = scores[best_id]
        if best_score <= 0:
            best_id = 1
            reason = "no strong emotional signal"
        else:
            reason = f"emotion score {best_score}"

        return MikoEmotion(best_id, self.EMOTIONS[best_id], reason)

    def image_path(self, emotion_id: int) -> Path:
        """Resolve the exact numbered emotion image, extracting the ZIP on demand."""
        emotion_id = max(1, min(24, int(emotion_id)))

        # Preferred runtime cache.
        cached = self.cache_dir / f"{emotion_id:02d}.webp"
        if cached.is_file():
            return cached

        # Already-installed assets in WEBP/PNG form.
        for candidate in (
            self.image_dir / f"{emotion_id:02d}.webp",
            self.image_dir / f"{emotion_id}.webp",
            self.image_dir / f"{emotion_id:02d}.png",
            self.image_dir / f"{emotion_id}.png",
        ):
            if candidate.is_file():
                return self._to_cached_webp(candidate, emotion_id) or candidate

        # One-time WispByte-friendly ZIP import.
        return self._extract_from_zip(emotion_id) or cached

    def _to_cached_webp(self, source: Path, emotion_id: int) -> Path | None:
        try:
            from PIL import Image

            self.cache_dir.mkdir(parents=True, exist_ok=True)
            target = self.cache_dir / f"{emotion_id:02d}.webp"
            with Image.open(source) as image:
                image = image.convert("RGB")
                image.thumbnail((512, 512), Image.Resampling.LANCZOS)
                image.save(target, "WEBP", quality=88, method=6)
            return target
        except (OSError, ValueError, ImportError) as exc:
            logger.warning(
                "Could not convert Miko emotion %02d from %s: %s",
                emotion_id,
                source,
                exc,
            )
            return None

    def _extract_from_zip(self, emotion_id: int) -> Path | None:
        zip_path = next((p for p in self.zip_candidates if p.is_file()), None)
        if zip_path is None:
            return None

        try:
            with zipfile.ZipFile(zip_path) as archive:
                selected: str | None = None
                for member in archive.namelist():
                    stem = Path(member).stem
                    if stem.isdigit() and int(stem) == emotion_id:
                        selected = member
                        break

                if selected is None:
                    return None

                raw = archive.read(selected)
                from PIL import Image

                self.cache_dir.mkdir(parents=True, exist_ok=True)
                target = self.cache_dir / f"{emotion_id:02d}.webp"
                with Image.open(io.BytesIO(raw)) as image:
                    image = image.convert("RGB")
                    image.thumbnail((512, 512), Image.Resampling.LANCZOS)
                    image.save(target, "WEBP", quality=88, method=6)

                return target
        except (OSError, ValueError, zipfile.BadZipFile, KeyError, ImportError) as exc:
            logger.warning("Could not load Miko emotion ZIP %s: %s", zip_path, exc)
            return None

    def available(self, emotion_id: int) -> bool:
        return self.image_path(emotion_id).is_file()

    def describe(self, emotion_id: int) -> str:
        emotion_id = max(1, min(24, int(emotion_id)))
        return self.EMOTIONS[emotion_id]
