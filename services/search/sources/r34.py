from __future__ import annotations

import asyncio
import aiohttp

from ..base import SourceAdapter
from ..models import MediaType, SearchResult


class R34Adapter(SourceAdapter):
    """Rule34 post-search adapter using the public DAPI endpoint."""

    name = "rule34"
    media_types = (MediaType.R34,)
    endpoint = "https://api.rule34.xxx/index.php"

    async def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        params = {
            "page": "dapi",
            "s": "post",
            "q": "index",
            "json": "1",
            "limit": str(min(max(limit, 1), 100)),
            "tags": query.strip(),
        }
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(self.endpoint, params=params) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)

        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []

        results = []
        for post in data:
            if not isinstance(post, dict) or not post.get("id"):
                continue
            post_id = str(post["id"])
            results.append(
                SearchResult(
                    title=f"Rule34 post #{post_id}",
                    media_type=MediaType.R34,
                    source=self.name,
                    source_id=post_id,
                    url=f"https://rule34.xxx/index.php?page=post&s=view&id={post_id}",
                    tags=str(post.get("tags", "")).split(),
                    adult=True,
                    image_url=post.get("file_url"),
                    metadata={
                        "rating": post.get("rating"),
                        "score": post.get("score"),
                        "source_url": post.get("source"),
                        "width": post.get("width"),
                        "height": post.get("height"),
                    },
                )
            )
        return results

    async def health(self) -> bool:
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self.endpoint, params={"page": "dapi", "s": "post", "q": "index", "limit": "1"}) as response:
                    return response.status < 500
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False
