from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from .base import SourceAdapter
from .models import MediaType, SearchResponse, SearchResult

logger = logging.getLogger(__name__)


class SearchEngine:
    """Run multiple source adapters concurrently and normalize their output."""

    def __init__(self, adapters: Iterable[SourceAdapter] = ()) -> None:
        self.adapters = list(adapters)

    def register(self, adapter: SourceAdapter) -> None:
        self.adapters.append(adapter)

    async def search(self, query: str, *, media_types: set[MediaType] | None = None, limit_per_source: int = 5) -> SearchResponse:
        query = query.strip()
        if not query:
            return SearchResponse(query="", results=[], sources=[])

        selected = [a for a in self.adapters if media_types is None or any(t in media_types for t in a.media_types)]

        async def run(adapter: SourceAdapter):
            try:
                return adapter.name, await adapter.search(query, limit=limit_per_source), None
            except Exception as exc:
                logger.exception("Search adapter %s failed", adapter.name)
                return adapter.name, [], str(exc)

        responses = await asyncio.gather(*(run(a) for a in selected))
        results: list[SearchResult] = []
        errors: dict[str, str] = {}
        for source, items, error in responses:
            results.extend(items)
            if error:
                errors[source] = error

        seen: set[tuple[str, str, str]] = set()
        unique: list[SearchResult] = []
        for item in results:
            key = (item.media_type.value, item.source, item.source_id or item.url or item.title.casefold())
            if key not in seen:
                seen.add(key)
                unique.append(item)

        unique.sort(key=lambda item: (not item.adult, item.title.casefold()))
        return SearchResponse(query=query, results=unique, sources=[a.name for a in selected], errors=errors)
