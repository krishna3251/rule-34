"""Behavior regressions for the repository audit; no real APIs or private data."""
import ast
import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord.ext import commands
from services.access_policy import content_access, prefix_access
from services.miko_memory import MikoMemory
from services.network_safety import PublicResolver, UnsafeURL, read_bounded, validate_url
from services.search.engine import SearchEngine
from services.search.models import MediaType, SearchResult

ROOT = Path(__file__).resolve().parents[1]


class MemoryTests(unittest.TestCase):
    def test_dm_guild_channel_isolation_and_clear_survive_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'memory.json'
            memory = MikoMemory(path)
            memory.add(1, 'user', 'private DM', None, 2)
            memory.add(1, 'user', 'guild A', 10, 20)
            self.assertEqual([m['content'] for m in memory.history(1, 10, 20)], ['guild A'])
            self.assertEqual(memory.history(1, 11, 20), [])
            memory.clear_channel(10, 20)
            memory = MikoMemory(path)
            self.assertEqual(memory.history(1, 10, 20), [])
            self.assertEqual(len(memory.history(1, None, 2)), 1)

    def test_legacy_global_history_is_preserved_but_never_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'memory.json'
            legacy = {'1': {'timestamp': time.time(), 'messages': [{'content': 'private'}]}}
            path.write_text(json.dumps({'users': legacy, 'conversations': {}}))
            memory = MikoMemory(path)
            self.assertEqual(memory.history(1, 10, 20), [])
            memory.save()
            self.assertEqual(json.loads(path.read_text())['legacy_users'], legacy)
            memory.reset(1)
            self.assertEqual(json.loads(path.read_text())['legacy_users'], {})

    def test_entry_expiry_and_user_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = MikoMemory(Path(tmp) / 'memory.json')
            with patch('services.miko_memory.time.time', return_value=10000):
                memory.add(1, 'user', 'expired', 10, 20)
            with patch('services.miko_memory.time.time', return_value=13000):
                memory.add(2, 'user', 'current', 10, 20)
                self.assertEqual([x['content'] for x in memory.history(1, 10, 20)], ['current'])
                memory.reset(2, 10, 20)
                self.assertEqual(memory.history(2, 10, 20), [])


class PermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_developer_commands_deny_nonowners(self):
        tree = ast.parse((ROOT / 'main.py').read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                 and n.name in {'dev_group', 'reload_command', 'sync_command', 'eval_command', 'shutdown_command'}]
        bot = commands.Bot(command_prefix='!', intents=discord.Intents.none())
        bot.is_owner = AsyncMock(return_value=False)
        scope = {'bot': bot, 'commands': commands, 'discord': discord}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'main.py', 'exec'), scope)
        ctx = NS(bot=bot, guild=None, channel=NS(), author=NS(id=10), command=None)
        for name in ['dev reload', 'dev sync', 'dev shutdown']:
            command = bot.get_command(name)
            with self.assertRaises(commands.NotOwner):
                await command.can_run(ctx)
        self.assertFalse(bot.get_command('dev eval').enabled)

    async def test_custom_prefix_is_a_list_without_await_error(self):
        tree = ast.parse((ROOT / 'main.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MikoBot')
        node = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_get_prefix')
        scope = {'commands': commands, 'Config': NS(PREFIX=commands.when_mentioned_or('~'))}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), scope)
        bot = NS(user=NS(id=4), cache=NS(guild_settings={10: {'prefix': '?'}}))
        self.assertIn('?', await scope['_get_prefix'](bot, bot, NS(guild=NS(id=10))))

    async def test_access_requires_scoped_verification_and_channel(self):
        bot = NS(db=NS(is_user_verified=Mock(return_value=True)))
        guild, user = NS(id=10), NS(id=1)
        self.assertFalse(await content_access(bot, None, None, user))
        self.assertFalse(await content_access(bot, guild, NS(is_nsfw=lambda: False), user))
        self.assertTrue(await content_access(bot, guild, NS(is_nsfw=lambda: True), user))
        bot.db.is_user_verified.assert_called_once_with(1, 10)
        bot.db.is_user_verified.return_value = False
        self.assertFalse(await content_access(bot, guild, NS(parent=NS(is_nsfw=lambda: True)), user))

    async def test_slash_owner_checks_are_registered(self):
        from cogs.index_admin import IndexAdminCog
        interaction = NS(client=NS(is_owner=AsyncMock(return_value=False)), user=NS(id=1))
        for command in [IndexAdminCog.indexstats, IndexAdminCog.sourcehealth]:
            self.assertEqual(len(command.checks), 1)
            self.assertFalse(await command.checks[0](interaction))
        from cogs.verification import VerificationCog
        with self.assertRaises(discord.app_commands.MissingPermissions):
            await VerificationCog.force_verify_slash.checks[0](NS(permissions=discord.Permissions.none()))


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_literal_and_invalid_urls_are_rejected(self):
        for url in ['http://127.0.0.1', 'http://[::1]', 'http://169.254.169.254',
                    'http://10.0.0.1', 'http://localhost', 'http://x.local',
                    'ftp://example.org', 'https://user:pass@example.org', 'https://example.org:9000']:
            with self.subTest(url=url), self.assertRaises(UnsafeURL):
                validate_url(url)
        self.assertEqual(validate_url('https://example.org/image.png'), 'https://example.org/image.png')

    async def test_dns_mixed_private_answers_are_rejected(self):
        resolver = PublicResolver()
        with patch('aiohttp.resolver.ThreadedResolver.resolve', new=AsyncMock(return_value=[{'host': '127.0.0.1'}])):
            with self.assertRaises(UnsafeURL):
                await resolver.resolve('example.org')
        await resolver.close()

    async def test_stream_reads_all_chunks_and_rejects_oversized_payload(self):
        class Content:
            async def iter_chunked(self, size):
                yield b'abc'
                yield b'def'
        response = NS(content=Content())
        self.assertEqual(await read_bounded(response, 6), b'abcdef')
        with self.assertRaises(ValueError):
            await read_bounded(response, 5)


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_separates_filters_and_rechecks_results(self):
        class Adapter:
            name = 'fake'
            media_types = (MediaType.ANIME, MediaType.GAME)
            search = AsyncMock(return_value=[SearchResult('Neutral fixture', MediaType.GAME, 'fake')])
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'INDEX_DB_PATH': str(Path(tmp) / 'index.db')}):
            adapter = Adapter()
            engine = SearchEngine([adapter])
            self.assertEqual(len((await engine.search('fixture')).results), 1)
            self.assertEqual((await engine.search('fixture', media_types={MediaType.ANIME})).results, [])
            self.assertEqual(len((await engine.search('fixture')).results), 1)


class MigrationTests(unittest.TestCase):
    def test_roleplay_legacy_is_preserved_and_new_tables_are_scoped(self):
        from cogs import roleplay_cog_sqlite as rp
        with tempfile.TemporaryDirectory() as tmp, patch.object(rp, 'DB_PATH', str(Path(tmp) / 'rp.db')):
            with sqlite3.connect(rp.DB_PATH) as conn:
                conn.execute('CREATE TABLE history (id INTEGER PRIMARY KEY, content TEXT)')
                conn.execute("INSERT INTO history VALUES (1, 'synthetic legacy')")
            rp.init_db()
            rp.init_db()  # Idempotent upgrades.
            with sqlite3.connect(rp.DB_PATH) as conn:
                self.assertEqual(conn.execute('SELECT content FROM history').fetchone()[0], 'synthetic legacy')
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM history_scoped').fetchone()[0], 0)
                conn.executemany("INSERT INTO config_scoped VALUES (?, 'rp_channel_id', ?)", [(10, '20'), (11, '21')])
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM config_scoped').fetchone()[0], 2)

    def test_health_is_unavailable_until_discord_is_ready(self):
        import keep_alive
        with patch.object(keep_alive, '_bot', None):
            self.assertEqual(keep_alive.app.test_client().get('/health').status_code, 503)
        with patch.object(keep_alive, '_bot', NS(is_ready=lambda: True, is_closed=lambda: False, failed_extensions=[])):
            self.assertEqual(keep_alive.app.test_client().get('/health').status_code, 200)


class ProfilePrivacyTests(unittest.TestCase):
    def test_unscoped_preferences_are_quarantined_except_safety_cap(self):
        from services.miko_profile import MikoProfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profile.json'
            path.write_text(json.dumps({'1': {'nickname': 'private fixture', 'level_cap': 0},
                                        '10:20:1': {'language': 'en'}}))
            profile = MikoProfile(path)
            self.assertEqual(profile.get(1, 11, 21), {'level_cap': 0})
            self.assertEqual(profile.get(1, 10, 20), {'level_cap': 0, 'language': 'en'})
            profile.forget(1)
            self.assertEqual(json.loads(path.read_text()), {})


class RoleplayBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_and_clear_are_scoped_by_guild(self):
        from cogs import roleplay_cog_sqlite as rp
        with tempfile.TemporaryDirectory() as tmp, patch.object(rp, 'DB_PATH', str(Path(tmp) / 'rp.db')):
            rp.init_db()
            cog = rp.RoleplaySQL(NS())
            for guild, channel in [(10, 20), (11, 21)]:
                interaction = NS(guild_id=guild, user='operator', response=NS(send_message=AsyncMock()))
                await rp.RoleplaySQL.setrpchannel.callback(cog, interaction, NS(id=channel, mention='channel'))
                self.assertEqual(await cog.get_rp_channel_id(guild), channel)
            self.assertEqual(await cog.get_rp_channel_id(10), 20)
            with sqlite3.connect(rp.DB_PATH) as conn:
                conn.executemany('INSERT INTO history_scoped(guild_id,channel_id,user_id,role,content) VALUES (?,?,?, ?,?)',
                                 [(10, 20, 1, 'user', 'one'), (11, 21, 1, 'user', 'two')])
            interaction = NS(guild_id=10, user='operator', response=NS(send_message=AsyncMock()))
            await rp.RoleplaySQL.clearhistory.callback(cog, interaction)
            with sqlite3.connect(rp.DB_PATH) as conn:
                self.assertEqual(conn.execute('SELECT guild_id FROM history_scoped').fetchall(), [(11,)])
