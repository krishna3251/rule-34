from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Iterable

from .base import SourceAdapter
from .models import MediaType, SearchResponse, SearchResult
from services.index.manager import IndexManager
from services.index.cache import IndexCache

logger = logging.getLogger(__name__)


class SearchEngine:
    """Unified search with source health, persistent cache and local indexing."""

    def __init__(self, adapters: Iterable[SourceAdapter] = ()) -> None:
        self.adapters = list(adapters)
        path = os.getenv("INDEX_DB_PATH", "rule43_index.db")
        self.manager = IndexManager(path)
        self.cache = IndexCache(path)

    def register(self, adapter: SourceAdapter) -> None:
        self.adapters.append(adapter)

    async def search(self, query: str, *, media_types: set[MediaType] | None = None,
                     limit_per_source: int = 5) -> SearchResponse:
        query = query.strip()
        if not query:
            return SearchResponse(query="", results=[], sources=[])

        cache_key = f"search:{query.casefold()}:{limit_per_source}"
        cached = self.cache.get(cache_key)
        if cached:
            results = [SearchResult(
                title=x["title"], media_type=MediaType(x["media_type"]), source=x["source"],
                source_id=x.get("source_id"), url=x.get("url"), description=x.get("description"),
                tags=x.get("tags", []), adult=x.get("adult", False), image_url=x.get("image_url"),
                metadata=x.get("metadata", {})) for x in cached]
            return SearchResponse(query=query, results=results, sources=[x.name for x in self.adapters])

        selected = [a for a in self.adapters
                    if (media_types is None or any(t in media_types for t in a.media_types))
                    and self.manager.should_query(a.name)]

        async def run(adapter: SourceAdapter):
            started = time.perf_counter()
            try:
                items = await adapter.search(query, limit=limit_per_source)
                self.manager.record_success(adapter.name, started)
                return adapter.name, items, None
            except Exception as exc:
                self.manager.record_failure(adapter.name, exc)
                logger.exception("Search adapter %s failed", adapter.name)
                return adapter.name, [], str(exc)

        responses = await asyncio.gather(*(run(a) for a in selected))
        results: list[SearchResult] = []
        errors: dict[str, str] = {}
        for source, items, error in responses:
            results.extend(items)
            if error:
                errors[source] = error
            for item in items:
                entity_id = self.manager.index.upsert_entity(
                    item.media_type.value, item.title, item.metadata
                )
                self.manager.index.upsert_source_record(
                    source=item.source, source_id=item.source_id, media_type=item.media_type.value,
                    title=item.title, url=item.url, entity_id=entity_id, metadata=item.metadata
                )

        seen: set[tuple[str, str, str]] = set()
        unique: list[SearchResult] = []
        for item in results:
            key = (item.media_type.value, item.source,
                   item.source_id or item.url or item.title.casefold())
            if key not in seen:
                seen.add(key)
                unique.append(item)

        unique.sort(key=lambda item: (not item.adult, item.title.casefold()))
        payload = [{
            "title": x.title, "media_type": x.media_type.value, "source": x.source,
            "source_id": x.source_id, "url": x.url, "description": x.description,
            "tags": x.tags, "adult": x.adult, "image_url": x.image_url, "metadata": x.metadata
        } for x in unique]
        if payload:
            self.cache.set(cache_key, payload, ttl=300)

        return SearchResponse(query=query, results=unique,
                              sources=[a.name for a in selected], errors=errors)
