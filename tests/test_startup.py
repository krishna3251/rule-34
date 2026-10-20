"""Load every extension in a disposable directory without Discord/API access."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def test_all_extensions_register_with_installed_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            for name in ['main.py', 'database.py', 'keep_alive.py']:
                shutil.copy2(ROOT / name, target / name)
            for name in ['cogs', 'services']:
                shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns('__pycache__'))
            script = '''
import asyncio
import socket
from unittest.mock import patch
import google.genai
import groq
import main

async def check():
    async with main.bot:
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network disabled during startup test')):
            await main.bot._load_extensions()
            assert not main.bot.failed_extensions, main.bot.failed_extensions
            assert len(main.bot.extensions) >= 10
            assert main.bot.get_command('dev eval').enabled is False
            assert main.bot.get_command('search') is not None
            assert main.bot.tree.get_command('search') is not None
            from services.access_policy import restricted
            assert restricted(main.bot.tree.get_command('game').get_command('search'))
            assert restricted(main.bot.get_command('search'))
            await asyncio.sleep(0)
        for name in list(main.bot.extensions):
            await main.bot.unload_extension(name)
asyncio.run(check())
'''
            # Explicit fake configuration; inherit only process basics, never credentials.
            env = {key: value for key, value in os.environ.items() if key in {'PATH', 'HOME', 'SYSTEMROOT', 'LANG'}}
            env.update(DISCORD_TOKEN='test-placeholder', OWNER_IDS='1', ENABLE_AUTO_BACKUP='false')
            result = subprocess.run([sys.executable, '-c', script], cwd=tmp, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout[-12000:])
