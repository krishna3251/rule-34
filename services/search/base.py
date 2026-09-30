from __future__ import annotations

from abc import ABC, abstractmethod

from .models import MediaType, SearchResult


class SourceAdapter(ABC):
    """Contract implemented by every external content source."""

    name: str
    media_types: tuple[MediaType, ...] = ()

    @abstractmethod
    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        """Search the source and return normalized results."""

    async def get(self, source_id: str) -> SearchResult | None:
        """Fetch one source record when the adapter supports it."""
        return None

    async def health(self) -> bool:
        """Return whether the source is currently reachable."""
        return True
