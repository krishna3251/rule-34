from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

logger = logging.getLogger("discord_bot")


@dataclass(frozen=True, slots=True)
class MikoEmotion:
    """Numeric Miko reaction mapped to one of the 24 canonical images."""

    id: int
    name: str
    reason: str = ""
    score: int = 0


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
            ("success", 3), ("jeet gaya", 5), ("jeet gayi", 5), ("jeet gaye", 5),
            ("got the job", 7),
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
            ("gn", 4), ("bed", 2), ("drowsy", 5), ("shubh ratri", 5), ("शुभ रात्रि", 5),
        ),
        2: (
            ("smile", 4), ("glad", 3), ("nice", 2), ("thanks", 2),
            ("thank you", 2), ("😊", 4), ("🙂", 3), ("namaste", 4),
            ("hello", 4), ("good morning", 4), ("good evening", 4), ("welcome", 3),
        ),
        4: (
            ("wink", 6), ("😉", 5), ("hehe", 2), ("cheeky", 3),
        ),
    }

    SERIOUS_MOOD_DEFAULTS = {
        "concerned": 15,
        "serious": 7,
    }

    MAX_BYTES = 8 * 1024 * 1024
    MAX_PIXELS = 16_000_000
    MIN_IMAGE_SCORE = 4
    FLIRT_EMOTIONS = frozenset({4, 6, 14, 21, 22})
    NEGATED = re.compile(r"\b(?:not|never|wasn't|weren't|didn't|don't|no longer)\s+(?:\w+\s+)?$")
    GREETING = re.compile(r"(?:hi|hey|hello|namaste|good morning|good evening)(?:[ ,]+(?:yae )?miko)?[!.]*")
    ARCHIVE_NAME = "Yae Miko Discord reaction images.zip"

    def __init__(self, image_dir: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[1]
        working_dir = Path.cwd()
        self.image_dir = Path(image_dir) if image_dir is not None else project_root / "assets/miko/emotions"
        self.patterns = {
            emotion_id: tuple((re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"), weight)
                              for phrase, weight in terms)
            for emotion_id, terms in self.PATTERNS.items()
        }
        archive_dirs = (working_dir, project_root, working_dir / "data", project_root / "data", self.image_dir)
        self.zip_candidates = tuple(directory / self.ARCHIVE_NAME for directory in dict.fromkeys(archive_dirs))

    @classmethod
    def _has_signal(cls, pattern: re.Pattern[str], text: str) -> bool:
        return any(not cls.NEGATED.search(text[max(0, match.start() - 40):match.start()])
                   for match in pattern.finditer(text))

    def analyze(
        self,
        user_text: str,
        reply_text: str,
        *,
        mood: str = "playful",
        intent: str = "chat",
        spice_level: int = 1,
    ) -> MikoEmotion:
        user = str(user_text or "").casefold().replace("’", "'")
        reply = str(reply_text or "").casefold().replace("’", "'")
        scores: dict[int, int] = {emotion_id: 0 for emotion_id in self.EMOTIONS}

        for emotion_id, patterns in self.patterns.items():
            if spice_level == 0 and emotion_id in self.FLIRT_EMOTIONS:
                continue
            for pattern, weight in patterns:
                if self._has_signal(pattern, reply):
                    scores[emotion_id] += weight
                elif self._has_signal(pattern, user):
                    scores[emotion_id] += (weight + 1) // 2

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
        elif intent == "chat" and self.GREETING.fullmatch(user.strip()):
            scores[2] += self.MIN_IMAGE_SCORE

        if mood in self.SERIOUS_MOOD_DEFAULTS:
            scores[self.SERIOUS_MOOD_DEFAULTS[mood]] += 4
        elif mood == "happy":
            scores[3] += 4
            scores[2] += 2

        if spice_level == 2 and scores[6] > 0:
            scores[6] += 1

        best_id = max(scores, key=scores.get)
        best_score = scores[best_id]
        if best_score <= 0:
            best_id = 1
            reason = "no strong emotional signal"
        else:
            reason = f"emotion score {best_score}"

        return MikoEmotion(best_id, self.EMOTIONS[best_id], reason, best_score)

    def public_image(self, emotion_id: int) -> bytes | None:
        """Return a general-audience reaction tied to the exact reviewed source bytes."""
        if emotion_id not in self.EMOTIONS:
            return None

        try:
            archives = [path for path in self.zip_candidates if path.is_file()]
            approved_archive = next((path for path in archives if path.with_suffix(".public.json").is_file()), None)
            archive_path = approved_archive or next(iter(archives), None)
            digest = None
            archive_only = False
            if approved_archive is not None:
                digest = self._approved_digest(approved_archive.with_suffix(".public.json"), emotion_id)
                archive_only = digest is not None
            if not archive_only:
                digest = self._approved_digest(self.image_dir / "public.json", emotion_id)
                # A paired host pack overrides only IDs listed in its approval file.
                if approved_archive is not None:
                    archive_path = None
            if digest is None:
                return None

            raw = self._read_source(emotion_id, archive_path, archive_only=archive_only)
            if not raw or len(raw) > self.MAX_BYTES or hashlib.sha256(raw).hexdigest() != digest:
                return None

            with Image.open(io.BytesIO(raw), formats=("WEBP", "PNG")) as image:
                if image.width * image.height > self.MAX_PIXELS or getattr(image, "is_animated", False):
                    return None
                image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
                image.thumbnail((512, 512), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, "WEBP", quality=88)
                return output.getvalue()
        except FileNotFoundError:
            return None
        except (OSError, ValueError, ImportError, RuntimeError, SyntaxError, zipfile.BadZipFile,
                Image.DecompressionBombError) as exc:
            logger.warning("Could not load Miko reaction %02d: %s", emotion_id, type(exc).__name__)
            return None

    @staticmethod
    def _approved_digest(path: Path, emotion_id: int) -> str | None:
        with path.open("rb") as manifest:
            contents = manifest.read(8193)
        if len(contents) > 8192:
            return None
        approved = json.loads(contents)
        digest = approved.get(f"{emotion_id:02d}") if isinstance(approved, dict) else None
        return digest if isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest) else None

    def _read_source(self, emotion_id: int, archive_path: Path | None, *, archive_only: bool) -> bytes | None:
        names = tuple(dict.fromkeys(f"{stem}.{ext}" for ext in ("webp", "png")
                                    for stem in (f"{emotion_id:02d}", str(emotion_id))))
        if not archive_only:
            for name in names:
                path = self.image_dir / name
                if path.is_file():
                    with path.open("rb") as source:
                        return source.read(self.MAX_BYTES + 1)

        if archive_path is None:
            return None
        with zipfile.ZipFile(archive_path) as archive:
            matches = [member for member in archive.infolist()
                       if not member.is_dir() and Path(member.filename).name.casefold() in names]
            if matches:
                member = min(matches, key=lambda item: (names.index(Path(item.filename).name.casefold()), item.filename))
                if member.file_size <= self.MAX_BYTES:
                    with archive.open(member) as source:
                        return source.read(self.MAX_BYTES + 1)
        return None
