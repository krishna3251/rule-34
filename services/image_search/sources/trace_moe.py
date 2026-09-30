from __future__ import annotations

import aiohttp

from ..base import ImageSource
from ..models import ImageMatch


class TraceMoeSource(ImageSource):
    """Anime frame identification through trace.moe."""

    name = "trace.moe"
    endpoint = "https://api.trace.moe/search"

    async def search_url(self, image_url: str, *, limit: int = 5) -> list[ImageMatch]:
        timeout = aiohttp.ClientTimeout(total=15)
        params = {"url": image_url}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(self.endpoint, params=params) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)

        results = []
        for item in data.get("result", [])[:limit]:
            similarity = item.get("similarity")
            results.append(
                ImageMatch(
                    source=self.name,
                    title=item.get("title") or "Unknown anime",
                    similarity=float(similarity) * 100 if similarity is not None else None,
                    url=item.get("ext_urls", [None])[0] if item.get("ext_urls") else item.get("url"),
                    thumbnail_url=item.get("image"),
                    episode=item.get("episode"),
                    timestamp=item.get("from"),
                    metadata={"anilist_id": str(item.get("anilist") or "")},
                )
            )
        return results
