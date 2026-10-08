from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ImageIntent:
    """Structured description of the reaction image Miko should use."""

    needed: bool
    image_type: str = "none"
    primary_emotion: str = "neutral"
    secondary_emotion: str | None = None
    action: str | None = None
    expression: str | None = None
    intensity: float = 0.0
    confidence: float = 0.0
    tags: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""
