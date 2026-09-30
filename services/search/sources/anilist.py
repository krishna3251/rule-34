from __future__ import annotations

import asyncio
import aiohttp

from ..base import SourceAdapter
from ..models import MediaType, SearchResult


class AniListAdapter(SourceAdapter):
    """AniList adapter for adult anime and manga metadata."""

    name = "anilist"
    media_types = (MediaType.ANIME, MediaType.MANGA)
    endpoint = "https://graphql.anilist.co"

    query = """
    query ($search: String!, $type: MediaType!, $perPage: Int!) {
      Page(page: 1, perPage: $perPage) {
        media(search: $search, type: $type, isAdult: true) {
          id
          title { romaji english native userPreferred }
          description(asHtml: false)
          genres
          tags { name rank isMediaSpoiler }
          isAdult
          coverImage { large }
          siteUrl
          format
          status
          episodes
          chapters
          volumes
        }
      }
    }
    """

    async def _search_type(self, session: aiohttp.ClientSession, query: str, media_type: str, limit: int):
        payload = {
            "query": self.query,
            "variables": {"search": query, "type": media_type, "perPage": min(max(limit, 1), 25)},
        }
        async with session.post(self.endpoint, json=payload) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)
        if data.get("errors"):
            raise RuntimeError(data["errors"][0].get("message", "AniList GraphQL error"))

        results = []
        for item in data.get("data", {}).get("Page", {}).get("media", []):
            titles = item.get("title", {})
            title = titles.get("userPreferred") or titles.get("romaji") or f"AniList #{item.get('id')}"
            kind = MediaType.ANIME if media_type == "ANIME" else MediaType.MANGA
            results.append(
                SearchResult(
                    title=title,
                    media_type=kind,
                    source=self.name,
                    source_id=str(item.get("id")),
                    url=item.get("siteUrl"),
                    description=item.get("description"),
                    tags=[t["name"] for t in item.get("tags", []) if isinstance(t, dict) and t.get("name")],
                    adult=bool(item.get("isAdult")),
                    image_url=item.get("coverImage", {}).get("large"),
                    metadata={
                        "format": item.get("format"),
                        "status": item.get("status"),
                        "episodes": item.get("episodes"),
                        "chapters": item.get("chapters"),
                        "volumes": item.get("volumes"),
                        "genres": item.get("genres", []),
                    },
                )
            )
        return results

    async def search(self, query: str, *, limit: int = 10):
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            anime, manga = await asyncio.gather(
                self._search_type(session, query, "ANIME", limit),
                self._search_type(session, query, "MANGA", limit),
            )
        return anime + manga

    async def health(self) -> bool:
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.endpoint, json={"query": "{ __typename }"}) as response:
                    return response.status < 500
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False
