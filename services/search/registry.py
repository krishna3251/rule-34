from __future__ import annotations

from .engine import SearchEngine
from .sources import AniListAdapter, ItchAdapter, R34Adapter


def build_search_engine() -> SearchEngine:
    engine = SearchEngine()
    engine.register(R34Adapter())
    engine.register(AniListAdapter())
    engine.register(ItchAdapter())
    return engine
