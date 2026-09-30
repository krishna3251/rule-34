from __future__ import annotations

import os

import aiohttp

from ..base import ImageSource
from ..models import ImageMatch


class SauceNAOSource(ImageSource):
    """SauceNAO reverse-image source.

    Requires SAUCENAO_API_KEY. The adapter only queries the normal API and
    returns indexed source metadata; it does not attempt to bypass access
    controls on source sites.
    """

    name = "SauceNAO"
    endpoint = "https://saucenao.com/search.php"

    async def search_url(self, image_url: str, *, limit: int = 5) -> list[ImageMatch]:
        api_key = os.getenv("SAUCENAO_API_KEY", "").strip()
        if not api_key:
            return []

        params = {
            "output_type": "2",
            "api_key": api_key,
            "url": image_url,
            "numres": str(min(max(limit, 1), 10)),
            "db": "999",
        }
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(self.endpoint, params=params) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)

        results = []
        for item in data.get("results", [])[:limit]:
            header = item.get("header", {})
            data_block = item.get("data", {})
            similarity = header.get("similarity")
            title = (
                data_block.get("title")
                or data_block.get("material")
                or data_block.get("source")
                or "Indexed image"
            )
            results.append(
                ImageMatch(
                    source=self.name,
                    title=str(title),
                    similarity=float(similarity) if similarity is not None else None,
                    url=data_block.get("ext_urls", [None])[0] if data_block.get("ext_urls") else None,
                    thumbnail_url=header.get("thumbnail"),
                    artist=data_block.get("member_name") or data_block.get("author_name"),
                    category=str(header.get("index_name") or ""),
                    metadata={
                        "index_id": str(header.get("index_id") or ""),
                        "index_name": str(header.get("index_name") or ""),
                    },
                )
            )
        return results
