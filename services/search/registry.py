from __future__ import annotations

from .engine import SearchEngine
from .sources import ItchAdapter


def build_search_engine() -> SearchEngine:
    engine = SearchEngine()
    engine.register(ItchAdapter())
    return engine
