from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MediaType(str, Enum):
    R34 = "r34"
    GAME = "game"
    MANGA = "manga"
    ANIME = "anime"


@dataclass(slots=True)
class SearchResult:
    title: str
    media_type: MediaType
    source: str
    source_id: str | None = None
    url: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    adult: bool = False
    image_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SearchResponse:
    query: str
    results: list[SearchResult]
    sources: list[str]
    errors: dict[str, str] = field(default_factory=dict)
