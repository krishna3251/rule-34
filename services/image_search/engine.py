from __future__ import annotations

import asyncio
import logging
import os
import time

from .base import ImageSource
from .models import ImageMatch
from services.index.manager import IndexManager
from services.index.cache import IndexCache

logger = logging.getLogger(__name__)


class ImageSourceEngine:
    """Reverse-image engine with source failover and persistent image evidence."""

    def __init__(self, sources: list[ImageSource] | None = None) -> None:
        self.sources = sources or []
        path = os.getenv("INDEX_DB_PATH", "rule43_index.db")
        self.manager = IndexManager(path)
        self.cache = IndexCache(path)

    def register(self, source: ImageSource) -> None:
        self.sources.append(source)

    async def search(self, image_url: str, *, limit: int = 5) -> list[ImageMatch]:
        cache_key = f"image:{image_url}:{limit}"
        cached = self.cache.get(cache_key)
        if cached:
            return [ImageMatch(**item) for item in cached]

        async def run(source: ImageSource):
            if not self.manager.should_query(source.name):
                return []
            started = time.perf_counter()
            try:
                matches = await source.search_url(image_url, limit=limit)
                self.manager.record_success(source.name, started)
                return matches
            except Exception as exc:
                self.manager.record_failure(source.name, exc)
                logger.exception("Image source %s failed", source.name)
                return []

        groups = await asyncio.gather(*(run(source) for source in self.sources))
        matches = [match for group in groups for match in group]
        matches.sort(key=lambda x: x.similarity if x.similarity is not None else 0, reverse=True)

        if matches:
            payload = [{
                "source": x.source, "title": x.title, "similarity": x.similarity,
                "url": x.url, "thumbnail_url": x.thumbnail_url, "episode": x.episode,
                "timestamp": x.timestamp, "artist": x.artist, "category": x.category,
                "metadata": x.metadata
            } for x in matches[:limit]]
            self.cache.set(cache_key, payload, ttl=1800)
        return matches
