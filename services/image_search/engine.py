from __future__ import annotations

import asyncio
import logging

from .base import ImageSource
from .models import ImageMatch

logger = logging.getLogger(__name__)


class ImageSourceEngine:
    def __init__(self, sources: list[ImageSource] | None = None) -> None:
        self.sources = sources or []

    def register(self, source: ImageSource) -> None:
        self.sources.append(source)

    async def search(self, image_url: str, *, limit: int = 5) -> list[ImageMatch]:
        async def run(source: ImageSource):
            try:
                return await source.search_url(image_url, limit=limit)
            except Exception:
                logger.exception("Image source %s failed", source.name)
                return []

        groups = await asyncio.gather(*(run(source) for source in self.sources))
        matches = [match for group in groups for match in group]
        matches.sort(key=lambda x: x.similarity if x.similarity is not None else 0, reverse=True)
        return matches
