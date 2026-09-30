from __future__ import annotations
import sqlite3
import time
from dataclasses import dataclass


@dataclass(slots=True)
class SourceState:
    source: str
    status: str
    consecutive_failures: int
    cooldown_until: float


class SourceHealth:
    def __init__(self, db_path: str = 'rule43_index.db', failure_threshold: int = 3, cooldown: int = 60) -> None:
        self.db_path = db_path
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown

    def _ensure(self, source: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('INSERT OR IGNORE INTO source_health(source) VALUES (?)', (source,))

    def allow(self, source: str) -> bool:
        self._ensure(source)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute('SELECT cooldown_until FROM source_health WHERE source=?', (source,)).fetchone()
            return not row or row[0] <= time.time()

    def record_success(self, source: str, latency_ms: float) -> None:
        self._ensure(source)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''UPDATE source_health SET status='healthy', success_count=success_count+1,
                avg_latency_ms=CASE WHEN success_count=0 THEN ? ELSE ((avg_latency_ms * success_count) + ?) / (success_count + 1) END,
                consecutive_failures=0, cooldown_until=0, last_error=NULL, last_checked=? WHERE source=?''',
                (latency_ms, latency_ms, time.time(), source))

    def record_failure(self, source: str, error: str) -> None:
        self._ensure(source)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute('SELECT consecutive_failures FROM source_health WHERE source=?', (source,)).fetchone()
            failures = int(row[0] if row else 0) + 1
            cooldown_until = time.time() + self.cooldown if failures >= self.failure_threshold else 0
            conn.execute('''UPDATE source_health SET status=?, failure_count=failure_count+1,
                consecutive_failures=?, cooldown_until=?, last_error=?, last_checked=? WHERE source=?''',
                ('cooldown' if cooldown_until else 'degraded', failures, cooldown_until, error[:500], time.time(), source))

    def states(self) -> list[SourceState]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute('SELECT source,status,consecutive_failures,cooldown_until FROM source_health ORDER BY source').fetchall()
            return [SourceState(*row) for row in rows]
