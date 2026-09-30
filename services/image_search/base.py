from __future__ import annotations

from abc import ABC, abstractmethod

from .models import ImageMatch


class ImageSource(ABC):
    name: str

    @abstractmethod
    async def search_url(self, image_url: str, *, limit: int = 5) -> list[ImageMatch]:
        """Reverse-search an externally reachable image URL."""
