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
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://itch.io", allow_redirects=True) as response:
                    return response.status < 500
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False
