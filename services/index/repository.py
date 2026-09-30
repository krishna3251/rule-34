from __future__ import annotations
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any


class LocalIndex:
    """SQLite-backed local knowledge index for normalized Rule43 data."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.getenv('INDEX_DB_PATH', 'rule43_index.db')
        self._lock = threading.RLock()
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with self._connect() as conn:
            conn.executescript('''
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    canonical_name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(kind, normalized_name)
                );
                CREATE TABLE IF NOT EXISTS aliases (
                    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    alias TEXT NOT NULL,
                    normalized_alias TEXT NOT NULL,
                    UNIQUE(entity_id, normalized_alias)
                );
                CREATE TABLE IF NOT EXISTS source_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
                    source TEXT NOT NULL,
                    source_id TEXT,
                    media_type TEXT,
                    title TEXT,
                    url TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    UNIQUE(source, source_id)
                );
                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sha256 TEXT UNIQUE,
                    phash TEXT,
                    width INTEGER,
                    height INTEGER,
                    mime_type TEXT,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS image_sources (
                    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                    source TEXT NOT NULL,
                    source_id TEXT,
                    url TEXT,
                    similarity REAL,
                    UNIQUE(image_id, source, source_id)
                );
                CREATE TABLE IF NOT EXISTS relationships (
                    from_entity INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    relation TEXT NOT NULL,
                    to_entity INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    confidence REAL DEFAULT 1.0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(from_entity, relation, to_entity)
                );
                CREATE TABLE IF NOT EXISTS cache (
                    cache_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_health (
                    source TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unknown',
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    avg_latency_ms REAL NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    cooldown_until REAL NOT NULL DEFAULT 0,
                    last_error TEXT,
                    last_checked REAL
                );
                CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(normalized_name);
                CREATE INDEX IF NOT EXISTS idx_aliases_name ON aliases(normalized_alias);
                CREATE INDEX IF NOT EXISTS idx_source_records_source ON source_records(source, source_id);
                CREATE INDEX IF NOT EXISTS idx_images_phash ON images(phash);
                CREATE INDEX IF NOT EXISTS idx_relationships_from ON relationships(from_entity, relation);
                CREATE INDEX IF NOT EXISTS idx_relationships_to ON relationships(to_entity, relation);
            ''')

    @staticmethod
    def normalize(value: str) -> str:
        return ' '.join(value.casefold().strip().split())

    def upsert_entity(self, kind: str, name: str, metadata: dict[str, Any] | None = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        normalized = self.normalize(name)
        payload = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        with self._lock, self._connect() as conn:
            conn.execute('''INSERT INTO entities(kind, canonical_name, normalized_name, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(kind, normalized_name) DO UPDATE SET
                metadata_json=excluded.metadata_json, updated_at=excluded.updated_at''',
                (kind, name.strip(), normalized, payload, now, now))
            row = conn.execute('SELECT id FROM entities WHERE kind=? AND normalized_name=?', (kind, normalized)).fetchone()
            return int(row['id'])

    def add_alias(self, entity_id: int, alias: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute('INSERT OR IGNORE INTO aliases(entity_id, alias, normalized_alias) VALUES (?, ?, ?)',
                         (entity_id, alias.strip(), self.normalize(alias)))

    def add_relationship(self, from_entity: int, relation: str, to_entity: int,
                         confidence: float = 1.0, metadata: dict[str, Any] | None = None) -> None:
        with self._lock, self._connect() as conn:
            conn.execute('''INSERT INTO relationships(from_entity, relation, to_entity, confidence, metadata_json)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(from_entity, relation, to_entity) DO UPDATE SET
                confidence=excluded.confidence, metadata_json=excluded.metadata_json''',
                (from_entity, relation, to_entity, max(0.0, min(1.0, confidence)),
                 json.dumps(metadata or {}, ensure_ascii=False)))

    def upsert_source_record(self, *, source: str, source_id: str | None, media_type: str,
                             title: str, url: str | None = None, entity_id: int | None = None,
                             metadata: dict[str, Any] | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            if source_id is None:
                conn.execute('INSERT INTO source_records(entity_id, source, media_type, title, url, metadata_json, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (entity_id, source, media_type, title, url, json.dumps(metadata or {}, ensure_ascii=False), now, now))
                return
            conn.execute('''INSERT INTO source_records(entity_id, source, source_id, media_type, title, url, metadata_json, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source, source_id) DO UPDATE SET
                entity_id=excluded.entity_id, media_type=excluded.media_type, title=excluded.title,
                url=excluded.url, metadata_json=excluded.metadata_json, last_seen=excluded.last_seen''',
                (entity_id, source, source_id, media_type, title, url, json.dumps(metadata or {}, ensure_ascii=False), now, now))

    def upsert_image(self, *, sha256: str, phash: str | None = None, width: int | None = None,
                     height: int | None = None, mime_type: str | None = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute('''INSERT INTO images(sha256, phash, width, height, mime_type, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(sha256) DO UPDATE SET
                phash=COALESCE(excluded.phash, images.phash), width=COALESCE(excluded.width, images.width),
                height=COALESCE(excluded.height, images.height), mime_type=COALESCE(excluded.mime_type, images.mime_type),
                last_seen=excluded.last_seen''', (sha256, phash, width, height, mime_type, now, now))
            row = conn.execute('SELECT id FROM images WHERE sha256=?', (sha256,)).fetchone()
            return int(row['id'])

    def add_image_source(self, image_id: int, source: str, source_id: str | None,
                         url: str | None, similarity: float | None = None) -> None:
        with self._lock, self._connect() as conn:
            conn.execute('''INSERT INTO image_sources(image_id, source, source_id, url, similarity)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(image_id, source, source_id) DO UPDATE SET
                url=excluded.url, similarity=excluded.similarity''',
                (image_id, source, source_id, url, similarity))

    def get_image_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM images WHERE sha256=?', (sha256,)).fetchone()
            return dict(row) if row else None

    def get_stats(self) -> dict[str, int]:
        with self._connect() as conn:
            tables = {'entities': 'entities', 'source_records': 'source_records', 'images': 'images',
                      'relationships': 'relationships', 'cache_entries': 'cache'}
            return {key: int(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])
                    for key, table in tables.items()}
