from __future__ import annotations

from .engine import ImageSourceEngine
from .sources.saucenao import SauceNAOSource
from .sources.trace_moe import TraceMoeSource


def build_image_source_engine() -> ImageSourceEngine:
    engine = ImageSourceEngine()
    engine.register(TraceMoeSource())
    engine.register(SauceNAOSource())
    return engine
