"""Opt-in Brain data in the bot's existing SQLite file; all callers use workers."""
from contextlib import contextmanager
import json
import re
import sqlite3
import time
import threading

from services.brain.skills import STYLES


class BrainStore:
    def __init__(self, path, retention_days=90):
        self.path = str(path)
        self.retention = retention_days * 86400
        self._lock = threading.RLock()
        self.ready = False
        self.fts = False

    @contextmanager
    def connection(self):
        self._lock.acquire()
        try:
            conn = sqlite3.connect(self.path, timeout=2)
        except BaseException:
            self._lock.release()
            raise
        conn.row_factory = sqlite3.Row
        try:
            if not self.ready:
                conn.executescript('''
                    CREATE TABLE IF NOT EXISTS miko_brain_preferences (
                        guild INTEGER, channel INTEGER, user INTEGER,
                        history INTEGER NOT NULL DEFAULT 0, external INTEGER NOT NULL DEFAULT 1,
                        style TEXT NOT NULL DEFAULT 'adaptive', PRIMARY KEY(guild,channel,user));
                    CREATE TABLE IF NOT EXISTS miko_brain_facts (
                        id INTEGER PRIMARY KEY, guild INTEGER, channel INTEGER, user INTEGER,
                        key TEXT NOT NULL, value TEXT NOT NULL, expires REAL NOT NULL,
                        UNIQUE(guild,channel,user,key));
                    CREATE INDEX IF NOT EXISTS miko_brain_fact_scope ON miko_brain_facts(guild,channel,user);
                    CREATE TABLE IF NOT EXISTS miko_brain_history (
                        id INTEGER PRIMARY KEY, guild INTEGER, channel INTEGER, user INTEGER,
                        turn TEXT NOT NULL, expires REAL NOT NULL);
                    CREATE INDEX IF NOT EXISTS miko_brain_history_scope ON miko_brain_history(guild,channel,user);
                    CREATE TABLE IF NOT EXISTS miko_brain_feedback (
                        id INTEGER PRIMARY KEY, guild INTEGER, channel INTEGER, user INTEGER,
                        helpful INTEGER NOT NULL, expires REAL NOT NULL);
                ''')
                try:
                    conn.executescript('''
                        CREATE VIRTUAL TABLE IF NOT EXISTS miko_brain_fts USING fts5(key,value,content='miko_brain_facts',content_rowid='id');
                        CREATE TRIGGER IF NOT EXISTS miko_brain_insert AFTER INSERT ON miko_brain_facts BEGIN
                            INSERT INTO miko_brain_fts(rowid,key,value) VALUES(new.id,new.key,new.value); END;
                        CREATE TRIGGER IF NOT EXISTS miko_brain_delete AFTER DELETE ON miko_brain_facts BEGIN
                            INSERT INTO miko_brain_fts(miko_brain_fts,rowid,key,value) VALUES('delete',old.id,old.key,old.value); END;
                        CREATE TRIGGER IF NOT EXISTS miko_brain_update AFTER UPDATE ON miko_brain_facts BEGIN
                            INSERT INTO miko_brain_fts(miko_brain_fts,rowid,key,value) VALUES('delete',old.id,old.key,old.value);
                            INSERT INTO miko_brain_fts(rowid,key,value) VALUES(new.id,new.key,new.value); END;
                    ''')
                    conn.execute("INSERT INTO miko_brain_fts(miko_brain_fts) VALUES('rebuild')")
                    self.fts = True
                except sqlite3.OperationalError:
                    self.fts = False
                conn.commit()
                self.ready = True
            now = time.time()
            for table in ('miko_brain_facts', 'miko_brain_history', 'miko_brain_feedback'):
                conn.execute(f'DELETE FROM {table} WHERE expires < ?', (now,))
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
            self._lock.release()

    def preferences(self, scope):
        with self.connection() as conn:
            row = conn.execute('SELECT history,external,style FROM miko_brain_preferences WHERE guild=? AND channel=? AND user=?', scope).fetchone()
            return dict(row) if row else {'history': False, 'external': True, 'style': 'adaptive'}

    def set_preferences(self, scope, *, history=None, external=None, style=None):
        prefs = self.preferences(scope)
        if style is not None and style not in STYLES:
            raise ValueError('Unknown conversation style')
        for key, value in [('history', history), ('external', external), ('style', style)]:
            if value is not None:
                prefs[key] = value
        with self.connection() as conn:
            conn.execute('INSERT OR REPLACE INTO miko_brain_preferences VALUES(?,?,?,?,?,?)',
                         (*scope, int(bool(prefs['history'])), int(bool(prefs['external'])), prefs['style']))
            if not prefs['history']:
                conn.execute('DELETE FROM miko_brain_history WHERE guild=? AND channel=? AND user=?', scope)
        return prefs

    def remember(self, scope, key, value):
        key, value = str(key).strip(), str(value).strip()
        if not 1 <= len(key) <= 64 or not 1 <= len(value) <= 1000:
            raise ValueError('Use a key of 1–64 characters and a value of 1–1000 characters.')
        with self.connection() as conn:
            conn.execute('''INSERT INTO miko_brain_facts(guild,channel,user,key,value,expires) VALUES(?,?,?,?,?,?)
                ON CONFLICT(guild,channel,user,key) DO UPDATE SET value=excluded.value,expires=excluded.expires''',
                         (*scope, key, value, time.time()+self.retention))
            conn.execute('''DELETE FROM miko_brain_facts WHERE guild=? AND channel=? AND user=? AND id NOT IN
                (SELECT id FROM miko_brain_facts WHERE guild=? AND channel=? AND user=? ORDER BY expires DESC,id DESC LIMIT 50)''', (*scope, *scope))

    def facts(self, scope, query='', limit=5):
        limit = max(1, min(50, limit))
        with self.connection() as conn:
            tokens = re.findall(r'[^\W_]+', query, re.UNICODE)[:12]
            if tokens and self.fts:
                expression = ' OR '.join('"'+word+'"' for word in tokens)
                rows = conn.execute('''SELECT f.key,f.value FROM miko_brain_fts JOIN miko_brain_facts f
                    ON f.id=miko_brain_fts.rowid WHERE miko_brain_fts MATCH ? AND f.guild=? AND f.channel=? AND f.user=?
                    ORDER BY rank LIMIT ?''', (expression, *scope, limit)).fetchall()
            elif tokens:
                rows = conn.execute('SELECT key,value FROM miko_brain_facts WHERE guild=? AND channel=? AND user=? LIMIT 50', scope).fetchall()
                wanted = {t.casefold() for t in tokens}
                def score(row):
                    words = set(re.findall(r'[^\W_]+', (row['key']+' '+row['value']).casefold()))
                    return len(wanted & words)
                rows = sorted((r for r in rows if score(r)), key=lambda r: -score(r))[:limit]
            else:
                rows = conn.execute('SELECT key,value FROM miko_brain_facts WHERE guild=? AND channel=? AND user=? ORDER BY expires DESC LIMIT ?', (*scope, limit)).fetchall()
            return [dict(row) for row in rows]

    def forget(self, scope, key=None):
        with self.connection() as conn:
            if key is None:
                conn.execute('DELETE FROM miko_brain_history WHERE guild=? AND channel=? AND user=?', scope)
                conn.execute('DELETE FROM miko_brain_feedback WHERE guild=? AND channel=? AND user=?', scope)
            conn.execute('DELETE FROM miko_brain_facts WHERE guild=? AND channel=? AND user=?' + (' AND key=?' if key is not None else ''),
                         (*scope, key) if key is not None else scope)

    def personal_context(self, scope, query):
        """Keep explicitly approved language/name preferences across topic changes."""
        with self.connection() as conn:
            rows = conn.execute('''SELECT key,value FROM miko_brain_facts WHERE guild=? AND channel=? AND user=?
                AND key IN ('language','preferred_language','nickname','preferred_name') ORDER BY key''', scope).fetchall()
            stable = [dict(row) for row in rows]
        relevant = self.facts(scope, query, 5)
        seen = {row['key'] for row in stable}
        return (stable + [row for row in relevant if row['key'] not in seen])[:5]

    def clear_history(self, scope):
        with self.connection() as conn:
            conn.execute("DELETE FROM miko_brain_history WHERE guild=? AND channel=? AND user=?", scope)

    def forget_user(self, user):
        # Keep privacy choices; forgetting must not turn external processing back on.
        with self.connection() as conn:
            for table in ('miko_brain_facts', 'miko_brain_history', 'miko_brain_feedback'):
                conn.execute(f'DELETE FROM {table} WHERE user=?', (user,))

    def history(self, scope):
        with self.connection() as conn:
            rows = conn.execute('SELECT turn FROM miko_brain_history WHERE guild=? AND channel=? AND user=? ORDER BY id DESC LIMIT 12', scope).fetchall()
            return [json.loads(r[0]) for r in reversed(rows)]

    def append(self, scope, turns):
        with self.connection() as conn:
            conn.executemany('INSERT INTO miko_brain_history(guild,channel,user,turn,expires) VALUES(?,?,?,?,?)',
                             [(*scope, json.dumps(t, ensure_ascii=False), time.time()+2700) for t in turns])
            conn.execute('''DELETE FROM miko_brain_history WHERE guild=? AND channel=? AND user=? AND id NOT IN
                (SELECT id FROM miko_brain_history WHERE guild=? AND channel=? AND user=? ORDER BY id DESC LIMIT 24)''', (*scope, *scope))

    def feedback(self, scope, helpful):
        with self.connection() as conn:
            conn.execute('INSERT INTO miko_brain_feedback(guild,channel,user,helpful,expires) VALUES(?,?,?,?,?)',
                         (*scope, int(helpful), time.time()+30*86400))
            conn.execute('DELETE FROM miko_brain_feedback WHERE id NOT IN (SELECT id FROM miko_brain_feedback ORDER BY id DESC LIMIT 1000)')
