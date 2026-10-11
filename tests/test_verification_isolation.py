import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cogs.verification import VerificationCog, VerifyView
from pathlib import Path

from database import Database


class VerificationIsolationTests(unittest.TestCase):
    def test_verified_users_are_scoped_to_guild(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "verification.db"))

            database.verify_user(42, guild_id=100)

            self.assertTrue(database.is_user_verified(42, guild_id=100))
            self.assertFalse(database.is_user_verified(42, guild_id=200))

            database.verify_user(42, guild_id=200)
            stats_a = database.get_verification_stats(100)
            stats_b = database.get_verification_stats(200)
            self.assertEqual(stats_a["total_verified"], 1)
            self.assertEqual(stats_b["total_verified"], 1)

    def test_legacy_table_migrates_without_global_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "legacy.db")
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "CREATE TABLE verified_users (user_id INTEGER PRIMARY KEY, "
                    "verified_at TIMESTAMP, verification_method TEXT)"
                )
                connection.execute(
                    "INSERT INTO verified_users VALUES (7, CURRENT_TIMESTAMP, 'reaction')"
                )
                connection.commit()

            database = Database(path)

            self.assertFalse(database.is_user_verified(7, guild_id=100))
            self.assertTrue(database.is_user_verified(7))

    def test_attempts_and_stats_are_scoped_to_guild(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "verification.db"))
            timestamp = datetime.now(timezone.utc).isoformat()
            database.log_verification_attempt(42, "member", False, "Cancelled", timestamp, guild_id=100)
            database.log_verification_attempt(42, "member", True, "Verified", timestamp, guild_id=200)
            self.assertEqual(database.get_verification_stats(100)["total_attempts"], 1)
            self.assertEqual(database.get_verification_stats(100)["failed_24h"], 1)
            self.assertEqual(database.get_verification_stats(200)["successful_attempts"], 1)
            self.assertEqual(database.get_verification_stats(200)["failed_24h"], 0)


class VerificationSessionIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        clock = patch("cogs.verification.time.time", return_value=1000)
        clock.start()
        self.addCleanup(clock.stop)
        self.user = SimpleNamespace(id=42, bot=False, send=AsyncMock())
        self.cog = VerificationCog(SimpleNamespace(db=Mock()))
        self.cog.assign_verification_roles = AsyncMock()
        self.cog.add_user_to_target_server = AsyncMock()
        self.cog.log_verification_attempt = AsyncMock()
        self.cog.safe_delete_message = AsyncMock()
        self.data_a = {"guild_id": 100, "timestamp": 1000, "token": "a", "message_id": 1}
        self.data_b = {"guild_id": 200, "timestamp": 1000, "token": "b", "message_id": 2}
        self.cog.pending_verifications = {(100, 42): self.data_a, (200, 42): self.data_b}

    async def test_reaction_selects_the_originating_guild(self):
        self.cog.complete_verification = AsyncMock()
        message = SimpleNamespace(id=2, embeds=[SimpleNamespace(title="Enhanced Age Verification Required")])
        await self.cog.on_reaction_add(SimpleNamespace(message=message, emoji="✅"), self.user)
        self.cog.complete_verification.assert_awaited_once_with(self.user, message, self.data_b)

    async def test_cancel_removes_only_originating_session(self):
        await self.cog.cancel_verification(self.user, self.data_b)
        self.assertIn((100, 42), self.cog.pending_verifications)
        self.assertNotIn((200, 42), self.cog.pending_verifications)
        self.cog.log_verification_attempt.assert_awaited_once_with(
            self.user, False, "User cancelled verification", 200
        )

    async def test_button_cannot_confirm_another_guild_or_replacement_session(self):
        view = VerifyView(self.cog, self.user, 100, "a")
        interaction = SimpleNamespace(user=self.user, guild_id=200, response=AsyncMock())
        await view.confirm.callback(interaction)
        self.cog.db.verify_user.assert_not_called()
        interaction.guild_id = 100
        self.data_a["token"] = "replacement"
        await view.confirm.callback(interaction)
        self.cog.db.verify_user.assert_not_called()

    async def test_rate_limits_are_guild_scoped(self):
        for _ in range(self.cog.max_attempts_per_hour):
            self.cog.add_rate_limit_attempt(42, 100)
        self.assertTrue(self.cog.is_rate_limited(42, 100))
        self.assertFalse(self.cog.is_rate_limited(42, 200))

    async def test_button_completes_only_the_bound_guild(self):
        view = VerifyView(self.cog, self.user, 200, "b")
        interaction = SimpleNamespace(user=self.user, guild_id=200, response=AsyncMock())
        await view.confirm.callback(interaction)
        self.cog.db.verify_user.assert_called_once_with(42, 200)
        self.cog.assign_verification_roles.assert_awaited_once_with(self.user, 200)
        self.assertIn((100, 42), self.cog.pending_verifications)
        self.assertNotIn((200, 42), self.cog.pending_verifications)

    async def test_reaction_completion_is_guild_scoped(self):
        await self.cog.complete_verification(self.user, SimpleNamespace(), self.data_b)
        self.cog.db.verify_user.assert_called_once_with(42, 200)
        self.cog.log_verification_attempt.assert_awaited_once_with(self.user, True, "Successfully verified", 200)
        self.assertIn((100, 42), self.cog.pending_verifications)
        self.assertNotIn((200, 42), self.cog.pending_verifications)


if __name__ == "__main__":
    unittest.main()
