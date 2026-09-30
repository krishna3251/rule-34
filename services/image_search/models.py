from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ImageMatch:
    source: str
    title: str
    similarity: float | None = None
    url: str | None = None
    thumbnail_url: str | None = None
    episode: int | None = None
    timestamp: float | None = None
    artist: str | None = None
    category: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
