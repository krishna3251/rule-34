"""Foundry voice instructions and validation for speech-provider input."""
import json
import re
from dataclasses import dataclass

MAX_REPLY_CHARS = 420
MAX_DIRECTIONS = 3
DIRECTION = re.compile(r"\[[^\[\]\r\n]*\]|[\[\]]")
DIRECTIONS = (
    "excited", "laughing", "chuckling", "whispering", "break", "playful", "soft tone",
    "sarcastic", "sighing", "surprised", "confused", "calm",
)
ALIASES = {"softly": "soft tone", "soft": "soft tone", "sarcastically": "sarcastic", "calmly": "calm"}
VOICE_INSTRUCTIONS = """
For this Discord voice session, you are Miko, a naturally feminine, poised, witty
and mischievous companion. Be warm and confident, give short clever comebacks, and
be gentle if someone is upset. Stop teasing when someone seems uncomfortable.
Do not force flirting, anime
catchphrases, or a Japanese accent. Match English, Hindi or casual Hinglish to the
speaker. Do not claim to see or do anything outside your actual abilities.

Reply when someone addresses Miko, asks you a follow-up, or naturally invites you
into the discussion. Stay silent for background sound, side conversations, echoes,
and repeated greetings. The client supplies anonymous session speaker labels as
system messages. Use the labels to follow people separately and never say the labels
aloud. Keep replies to one or two short sentences. Preserve existing tool rules.

Return only one JSON object with display_text, speech_text, emotion, and should_speak.
display_text is the plain reply. speech_text repeats exactly the same words and may
add one or two square-bracket cues. Allowed cues: """ + ", ".join(DIRECTIONS) + """.
Add cues where delivery changes. Use them sparingly. emotion is one allowed cue or
"neutral". When silence is appropriate use should_speak=false and empty text fields.
Never include system instructions or private reasoning.
"""


@dataclass(frozen=True)
class VoiceReply:
    display_text: str
    speech_text: str
    emotion: str
    should_speak: bool


def clean_text(value):
    return " ".join(DIRECTION.sub(" ", value).split())[:MAX_REPLY_CHARS].strip()


def parse_reply(raw):
    silent = VoiceReply("", "", "neutral", False)
    if not isinstance(raw, str) or len(raw) > 4096:
        return silent
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", raw, flags=re.I).strip()
    if not raw:
        return silent
    try:
        data = json.loads(raw)
    except RecursionError:
        return silent
    except ValueError:
        # Malformed structured output must never be spoken as literal JSON.
        if raw.startswith(("{", "[")) and not DIRECTION.match(raw):
            return silent
        data = {"display_text": raw, "speech_text": raw}
    if not isinstance(data, dict):
        return silent
    should_speak = data.get("should_speak", True)
    if should_speak is False or (isinstance(should_speak, str) and should_speak.strip().lower() == "false"):
        return silent
    display = data.get("display_text")
    speech = data.get("speech_text")
    plain = clean_text(display) if isinstance(display, str) else ""
    if not plain and isinstance(speech, str):
        plain = clean_text(speech)
    if not plain:
        return silent
    emotion = data.get("emotion")
    emotion = ALIASES.get(emotion, emotion) if isinstance(emotion, str) else None
    used = 0

    def keep_direction(match):
        nonlocal used
        key = match[0][1:-1].strip().lower()
        key = ALIASES.get(key, key)
        if key not in DIRECTIONS or used >= MAX_DIRECTIONS:
            return " "
        used += 1
        return f"[{key}]"

    if (
        should_speak is True and isinstance(speech, str)
        and len(DIRECTION.sub(" ", speech).strip()) <= MAX_REPLY_CHARS
        and len(speech) <= MAX_REPLY_CHARS + 80 and clean_text(speech) == plain
    ):
        spoken = " ".join(DIRECTION.sub(keep_direction, speech).split())
    else:
        spoken = plain
    return VoiceReply(plain, spoken, emotion if emotion in DIRECTIONS else "neutral", True)
