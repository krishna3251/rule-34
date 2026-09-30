from __future__ import annotations
import os
from .health import SourceHealth
from .repository import LocalIndex


class IndexManager:
    def __init__(self, db_path: str | None = None) -> None:
        path = db_path or os.getenv('INDEX_DB_PATH', 'rule43_index.db')
        self.index = LocalIndex(path)
        self.health = SourceHealth(path)

    def stats(self) -> dict[str, int]:
        return self.index.get_stats()

    def health_rows(self):
        return self.health.states()

    def should_query(self, source: str) -> bool:
        return self.health.allow(source)

    def record_success(self, source: str, started: float) -> None:
        import time
        self.health.record_success(source, (time.perf_counter() - started) * 1000)

    def record_failure(self, source: str, error: Exception) -> None:
        self.health.record_failure(source, str(error))
