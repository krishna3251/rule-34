from __future__ import annotations

import asyncio

import aiohttp

from ..base import SourceAdapter
from ..models import MediaType, SearchResult


class ItchAdapter(SourceAdapter):
    """Itch metadata adapter using normally exposed public data only."""

    name = "itch"
    media_types = (MediaType.GAME,)

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        # Keep this conservative until an official/allowed search endpoint is configured.
        return []

    async def health(self) -> bool:
        # Search is deliberately unavailable until a supported endpoint is configured.
        return False
