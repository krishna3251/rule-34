from __future__ import annotations
import json
import sqlite3
import time


class IndexCache:
    def __init__(self, db_path: str = 'rule43_index.db') -> None:
        self.db_path = db_path

    def get(self, key: str):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute('SELECT value_json, expires_at FROM cache WHERE cache_key=?', (key,)).fetchone()
            if not row:
                return None
            if row[1] <= time.time():
                conn.execute('DELETE FROM cache WHERE cache_key=?', (key,))
                return None
            return json.loads(row[0])

    def set(self, key: str, value, ttl: int = 300) -> None:
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''INSERT INTO cache(cache_key, value_json, expires_at, created_at)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(cache_key) DO UPDATE SET value_json=excluded.value_json,
                            expires_at=excluded.expires_at, created_at=excluded.created_at''',
                         (key, json.dumps(value, ensure_ascii=False), now + max(1, ttl), now))

    def delete_expired(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute('DELETE FROM cache WHERE expires_at <= ?', (time.time(),))
            return cur.rowcount
