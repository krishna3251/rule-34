"""Scoped opt-in and real chat-entry routing without provider requests."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from cogs.miko_chat import MikoChat
from services.miko_chat_engine import MikoChatEngine
from services.miko_decision import MikoDecisionEngine
from services.miko_gate import MikoGate
from services.miko_memory import MikoMemory
from services.miko_profile import MikoProfile


class FlirtControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = MikoChatEngine.__new__(MikoChatEngine)
        self.engine.profile = MikoProfile(Path(self.tmp.name) / 'profile.json')
        self.engine.memory = MikoMemory(Path(self.tmp.name) / 'memory.json')
        self.engine.gate = MikoGate()
        self.engine.gate.USER_COOLDOWN = self.engine.gate.CHANNEL_COOLDOWN = 0
        self.engine.decision = MikoDecisionEngine()
        self.engine.turn_counts = {}
        self.engine._generate_and_finalize = AsyncMock()
        self.bot = NS(db=NS(is_user_verified=Mock(return_value=True)),
                      get_context=AsyncMock(return_value=NS(valid=False)), user=NS(id=99))
        self.message = NS(author=NS(id=1, bot=False, display_name='User'), guild=NS(id=10),
                          channel=NS(id=20, is_nsfw=lambda: True), content='miko hello', reference=None)
        self.cog = MikoChat.__new__(MikoChat)
        self.cog.bot = self.bot
        self.cog.chat_engine = self.engine
        self.cog.channel_levels = {}

    async def decision_for(self, direct=False, admin_level=2):
        if direct:
            await self.engine.ask_direct(self.message, self.bot, prompt='hello', admin_level=admin_level)
        else:
            await self.engine.handle(self.message, self.bot, admin_level=admin_level)
        return self.engine._generate_and_finalize.call_args.kwargs['decision']

    async def test_default_is_clean_for_both_entry_points(self):
        for direct in [False, True]:
            self.assertEqual((await self.decision_for(direct)).spice_level, 0)

    async def test_verified_scoped_opt_in_enables_existing_tone(self):
        result = await self.cog._flirt_on(self.message.guild, self.message.channel, self.message.author)
        self.assertIn('is on', result)
        for direct in [False, True]:
            self.assertEqual((await self.decision_for(direct)).spice_level, 2)
            self.assertEqual((await self.decision_for(direct, admin_level=1)).spice_level, 1)
        self.engine.profile = MikoProfile(self.engine.profile.path)
        self.assertEqual((await self.decision_for()).spice_level, 2)

    async def test_enable_denied_without_access_or_admin_permission(self):
        self.bot.db.is_user_verified.return_value = False
        self.assertIn('/verify', await self.cog._flirt_on(self.message.guild, self.message.channel, self.message.author))
        self.bot.db.is_user_verified.return_value = True
        self.cog.channel_levels[20] = 0
        self.assertIn('administrator', await self.cog._flirt_on(self.message.guild, self.message.channel, self.message.author))
        self.assertFalse(self.engine.profile.get(1, 10, 20).get('flirt_opt_in', False))

    async def test_opt_in_does_not_cross_channels_servers_or_users(self):
        self.engine.profile.enable_flirting(1, 10, 20)
        for author, guild, channel in [(1, 10, 21), (1, 11, 20), (2, 10, 20)]:
            message = NS(author=NS(id=author), guild=NS(id=guild), channel=NS(id=channel, is_nsfw=lambda: True))
            self.assertEqual((await self.engine.conversation_profile(message, self.bot))['level_cap'], 0)

    async def test_revoked_verification_channel_and_dm_stay_clean(self):
        self.engine.profile.enable_flirting(1, 10, 20)
        self.bot.db.is_user_verified.return_value = False
        self.assertEqual((await self.decision_for(True)).spice_level, 0)
        self.bot.db.is_user_verified.return_value = True
        self.message.channel.is_nsfw = lambda: False
        self.assertEqual((await self.decision_for()).spice_level, 0)
        self.message.guild = None
        self.assertEqual((await self.decision_for(True)).spice_level, 0)

    async def test_thread_uses_parent_access_and_level_but_separate_consent(self):
        parent = self.message.channel
        self.message.channel = NS(id=21, parent=parent)
        self.cog.channel_levels[20] = 2
        self.assertEqual(self.cog._channel_level(self.message.channel), 2)
        self.engine.profile.enable_flirting(1, 10, 21)
        self.assertEqual((await self.decision_for(True)).spice_level, 2)

    async def test_off_anywhere_clears_all_opt_ins_and_survives_reload(self):
        self.engine.profile.enable_flirting(1, 10, 20)
        self.engine.profile.enable_flirting(1, 11, 30)
        await self.cog._flirt_off(self.message.author)
        self.engine.profile = MikoProfile(self.engine.profile.path)
        self.assertEqual((await self.decision_for()).spice_level, 0)
        self.engine.profile.enable_flirting(1, 10, 20)
        self.assertFalse(self.engine.profile.get(1, 11, 30).get('flirt_opt_in', False))

    async def test_natural_language_stop_clears_all_opt_ins(self):
        self.engine.profile.enable_flirting(1, 10, 20)
        self.engine.profile.enable_flirting(1, 11, 30)
        self.message.content = 'miko stop flirting please'
        self.assertEqual((await self.decision_for()).spice_level, 0)
        self.assertFalse(self.engine.profile.get(1, 11, 30).get('flirt_opt_in', False))

    async def test_failed_save_does_not_enable_flirting(self):
        with patch.object(self.engine.profile, 'save', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                await self.cog._flirt_on(self.message.guild, self.message.channel, self.message.author)
        self.assertEqual((await self.decision_for()).spice_level, 0)

    async def test_slash_toggle_replies_are_private(self):
        interaction = NS(guild=self.message.guild, channel=self.message.channel, user=self.message.author,
                         response=NS(defer=AsyncMock()), followup=NS(send=AsyncMock()))
        await MikoChat.flirt_on_slash.callback(self.cog, interaction)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        self.assertTrue(interaction.followup.send.call_args.kwargs['ephemeral'])
        await MikoChat.flirt_off_slash.callback(self.cog, interaction)
        self.assertTrue(interaction.followup.send.call_args.kwargs['ephemeral'])

    async def test_opt_out_during_generation_discards_flirty_result(self):
        from services.miko_decision import MikoDecision
        from services.miko_response import MikoResponseProcessor
        self.engine.profile.enable_flirting(1, 10, 20)
        self.engine.context = NS(build=Mock(return_value=[{'role': 'user', 'content': 'hello'}]))
        self.engine._adaptive_temperature = Mock(return_value=0.8)
        self.engine.response = MikoResponseProcessor()
        self.engine.emotion = NS(analyze=Mock(return_value=NS(id=1, name='Neutral')))
        calls = 0

        async def generate(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.engine.profile.disable_flirting(1)
                return 'Smooth, darling.', False
            self.assertTrue(kwargs['strict'])
            return 'Okay. Keeping things friendly.', False

        self.engine._agent_generate = generate
        decision = MikoDecision(True, prompt='hello', spice_level=2)
        result = await MikoChatEngine._generate_and_finalize(
            self.engine, message=self.message, bot=self.bot, decision=decision,
            history=[], profile={}, display_name='User', conversation_key='10:20', turn=1)
        self.assertEqual(calls, 2)
        self.assertEqual(result.text, 'Okay. Keeping things friendly.')
        self.assertEqual(self.engine.emotion.analyze.call_args.kwargs['spice_level'], 0)
        self.assertNotIn('Smooth, darling.', [m['content'] for m in self.engine.memory.history(1, 10, 20)])
