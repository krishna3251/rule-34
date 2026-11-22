"""Test conversation routing, status and reaction assets without Discord or model calls."""

import asyncio
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import discord
from PIL import Image

from cogs.miko_chat import MikoChat
from services.miko_ai import MikoAI, MikoAIResponse
from services.miko_chat_engine import MikoChatEngine, MikoResult
from services.miko_emotion import MikoEmotionEngine
from services.miko_profile import MikoProfile


class ChatRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.engine = MikoChatEngine(profile=MikoProfile(Path(tmp.name) / "profile.json"))
        self.addAsyncCleanup(self.engine.close)
        self.engine.gate.USER_COOLDOWN = self.engine.gate.CHANNEL_COOLDOWN = 0
        self.engine.ai.generate_agent = AsyncMock(return_value=MikoAIResponse("I hear you.", {}))
        self.bot = NS(user=NS(id=99), get_context=AsyncMock(return_value=NS(valid=False)),
                      db=NS(is_user_verified=Mock(return_value=True)))
        self.channel = NS(id=20, is_nsfw=lambda: True)
        self.message = NS(author=NS(id=1, bot=False, display_name="User"), guild=NS(id=10),
                          channel=self.channel, content="")

    async def test_auto_chat_uses_message_and_keeps_distress_out_of_search(self):
        self.engine.profile.enable_flirting(1, 10, 20)
        self.message.content = "I had a rough day today. Even Valorant felt exhausting."
        on_wait = Mock()
        result = await self.engine.handle(self.message, self.bot, auto_chat=True, admin_level=2, on_wait=on_wait)
        messages = self.engine.ai.generate_agent.call_args.args[0]
        self.assertEqual(messages[-1]["content"], "[User] " + self.message.content)
        self.assertEqual(result.intent, "serious")
        self.assertFalse(result.attach_emotion)
        self.assertIn("CURRENT CHAT LEVEL: 0", messages[0]["content"])
        self.assertIn("listen before offering advice", messages[0]["content"])
        self.assertFalse(self.engine.ai.generate_agent.call_args.kwargs["allow_web"])
        on_wait.assert_not_called()

    async def test_clarification_uses_earlier_turn_and_has_no_flirting(self):
        self.engine.profile.enable_flirting(1, 10, 20)
        await self.engine.ask_direct(self.message, self.bot, prompt="Use the blue version.", admin_level=2)
        self.engine.ai.generate_agent.return_value = MikoAIResponse("Got it. The blue text only.", {})
        result = await self.engine.ask_direct(
            self.message, self.bot, prompt="Mera matlab tha blue text only.", admin_level=2)
        messages = self.engine.ai.generate_agent.call_args.args[0]
        self.assertIn("Use the blue version.", str(messages[1:-1]))
        self.assertEqual(result.response_mode, "clarification")
        self.assertIn("CURRENT CHAT LEVEL: 0", messages[0]["content"])
        self.assertFalse(result.attach_emotion)

    async def test_celebration_can_attach_but_clean_chat_skips_romantic_image(self):
        self.engine.ai.generate_agent.return_value = MikoAIResponse("Congrats! That was a clean win.", {})
        result = await self.engine.ask_direct(self.message, self.bot, prompt="Jeet gaya!")
        self.assertEqual(result.emotion_id, 3)
        self.assertTrue(result.attach_emotion)
        self.engine.ai.generate_agent.return_value = MikoAIResponse("I appreciate the kind words.", {})
        result = await self.engine.ask_direct(self.message, self.bot, prompt="Love you so much ❤️")
        self.assertFalse(result.attach_emotion)

    async def test_simple_greeting_and_good_news_can_attach_with_plain_replies(self):
        for prompt, reply, expected in (
            ("Hey, Miko!", "How are you doing?", 2),
            ("I got the job!", "You earned it.", 3),
        ):
            with self.subTest(prompt=prompt):
                self.engine.ai.generate_agent.return_value = MikoAIResponse(reply, {})
                result = await self.engine.ask_direct(self.message, self.bot, prompt=prompt)
                self.assertTrue(result.attach_emotion)
                self.assertEqual(result.emotion_id, expected)

    async def test_technical_chat_stays_clean_and_text_only(self):
        self.engine.profile.enable_flirting(1, 10, 20)
        self.engine.ai.generate_agent.return_value = MikoAIResponse("Great news. Python can handle that.", {})
        result = await self.engine.ask_direct(self.message, self.bot, prompt="How do Python lists work?", admin_level=2)
        system = self.engine.ai.generate_agent.call_args.args[0][0]["content"]
        self.assertIn("CURRENT CHAT LEVEL: 0", system)
        self.assertFalse(result.attach_emotion)

    async def test_disabled_direct_request_and_local_calculation_do_not_start_wait(self):
        on_wait = Mock()
        result = await self.engine.ask_direct(self.message, self.bot, prompt="Hello", disabled=True, on_wait=on_wait)
        self.assertEqual(result.reason, "channel_disabled")
        result = await self.engine.ask_direct(self.message, self.bot, prompt="calculate 2+3", on_wait=on_wait)
        self.assertEqual(result.text, "5")
        self.engine.ai.generate_agent.assert_not_awaited()
        on_wait.assert_not_called()

    async def test_provider_fallback_does_not_complete_or_select_art(self):
        self.engine.ai.generate_agent.return_value = MikoAI._unavailable()
        result = await self.engine.ask_direct(self.message, self.bot, prompt="Hello")
        self.assertIn("unavailable", result.text)
        self.assertFalse(result.completed)
        self.assertFalse(result.attach_emotion)

    async def test_admin_tone_downgrade_during_generation_causes_clean_retry(self):
        self.engine.profile.enable_flirting(1, 10, 20)
        level = Mock(return_value=2)
        self.engine.miko_chat = NS(_channel_level=level)

        async def generate(messages, **kwargs):
            if not kwargs["strict"]:
                level.return_value = 1
                return MikoAIResponse("A reply under the old settings.", {})
            self.assertIn("CURRENT CHAT LEVEL: 0", messages[0]["content"])
            return MikoAIResponse("A clean answer.", {})

        self.engine.ai.generate_agent.side_effect = generate
        result = await self.engine.ask_direct(self.message, self.bot, prompt="Let's chat", admin_level=2)
        self.assertEqual(result.text, "A clean answer.")
        self.assertFalse(result.attach_emotion)
        self.assertEqual(self.engine.ai.generate_agent.await_count, 2)


class ReactionAssetTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.engine = MikoEmotionEngine(self.root)
        self.engine.zip_candidates = (self.root / "reactions.zip",)
        with io.BytesIO() as buffer:
            Image.new("RGBA", (700, 700), (255, 200, 240, 0)).save(buffer, "PNG")
            self.picture = buffer.getvalue()

    def approve(self, raw):
        (self.root / "public.json").write_text(json.dumps({"02": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")

    def test_approval_is_bound_to_exact_bytes_and_keeps_transparency(self):
        path = self.root / "02.png"
        path.write_bytes(self.picture)
        self.assertIsNone(self.engine.public_image(2))
        self.approve(self.picture)
        with Image.open(io.BytesIO(self.engine.public_image(2))) as rendered:
            self.assertEqual(rendered.format, "WEBP")
            self.assertEqual(rendered.size, (512, 512))
            self.assertEqual(rendered.getpixel((0, 0))[3], 0)
        path.write_bytes(self.picture + b"modified")
        self.assertIsNone(self.engine.public_image(2))

    def test_zip_members_are_read_without_extracting_and_bad_files_are_skipped(self):
        self.approve(self.picture)
        with zipfile.ZipFile(self.engine.zip_candidates[0], "w") as archive:
            archive.writestr("nested/02.png", self.picture)
        self.assertIsNotNone(self.engine.public_image(2))
        self.assertFalse((self.root / "nested").exists())
        corrupt = b"not an image"
        (self.root / "02.png").write_bytes(corrupt)
        self.approve(corrupt)
        self.assertIsNone(self.engine.public_image(2))

    def test_host_pack_uses_its_own_approval_instead_of_the_loose_file(self):
        loose_picture = self.picture + b"loose"
        (self.root / "02.png").write_bytes(loose_picture)
        self.approve(loose_picture)
        archive_path = self.engine.zip_candidates[0]
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("nested/02.png", self.picture)
        archive_path.with_suffix(".public.json").write_text(
            json.dumps({"02": hashlib.sha256(self.picture).hexdigest()}), encoding="utf-8"
        )
        self.assertIsNotNone(self.engine.public_image(2))
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("nested/02.png", self.picture + b"modified")
        self.assertIsNone(self.engine.public_image(2))

    def test_partial_host_pack_preserves_bundled_moods(self):
        (self.root / "02.png").write_bytes(self.picture)
        self.approve(self.picture)
        archive_path = self.engine.zip_candidates[0]
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("03.png", self.picture)
        archive_path.with_suffix(".public.json").write_text(
            json.dumps({"03": hashlib.sha256(self.picture).hexdigest()}), encoding="utf-8"
        )

        self.assertIsNotNone(self.engine.public_image(2))
        self.assertIsNotNone(self.engine.public_image(3))

    def test_bundled_pack_resolves_all_distinct_emotions(self):
        engine = MikoEmotionEngine()
        engine.zip_candidates = ()
        images = [engine.public_image(emotion_id) for emotion_id in engine.EMOTIONS]
        self.assertNotIn(None, images)
        self.assertEqual(len(set(images)), 24)

    def test_word_boundaries_and_negation_avoid_false_visual_matches(self):
        emotion = self.engine.analyze("make a screenshot", "The signal is weak.", spice_level=0)
        self.assertEqual(emotion.id, 1)
        emotion = self.engine.analyze("I am not happy", "You don't sound happy.", spice_level=0)
        self.assertLess(emotion.score, self.engine.MIN_IMAGE_SCORE)


class DiscordDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = MikoChat.__new__(MikoChat)
        self.cog.WAIT_SECONDS = 0
        self.cog.bot = NS(user=NS(id=99))
        self.cog.recent_images = OrderedDict()
        self.cog.chat_engine = NS(emotion=NS(public_image=Mock(return_value=b"approved test bytes")))
        self.source = NS(add_reaction=AsyncMock(), remove_reaction=AsyncMock())
        self.post = NS(add_reaction=AsyncMock())
        self.send = AsyncMock(return_value=self.post)
        self.message = NS(guild=NS(id=10), channel=NS(id=20), author=NS(id=1))

    async def test_slow_success_reacts_on_source_and_cleans_up(self):
        sequence = []
        self.source.add_reaction.side_effect = lambda emoji: sequence.append(emoji)
        self.source.remove_reaction.side_effect = lambda emoji, user: sequence.append("removed")
        self.send.side_effect = lambda *a, **kwargs: sequence.append("sent") or self.post

        async def request(on_wait):
            on_wait()
            await asyncio.sleep(.01)
            return MikoResult(True, "Done.", completed=True)

        await self.cog._deliver_chat(request, self.send, self.message, source=self.source)
        self.assertEqual(sequence, ["⏳", "sent", "removed", "✅"])

    async def test_fast_reply_does_not_add_wait_or_tick(self):
        self.cog.WAIT_SECONDS = 2

        async def request(on_wait):
            on_wait()
            return MikoResult(True, "Done.", completed=True)

        await self.cog._deliver_chat(request, self.send, self.message, source=self.source)
        self.source.add_reaction.assert_not_awaited()
        self.source.remove_reaction.assert_not_awaited()
        self.send.assert_awaited_once()

    async def test_timeout_clears_wait_without_tick(self):
        async def request(on_wait):
            on_wait()
            await asyncio.sleep(.01)
            return MikoResult(False, reason="timeout")

        await self.cog._deliver_chat(request, self.send, self.message, source=self.source)
        self.source.add_reaction.assert_awaited_once_with("⏳")
        self.source.remove_reaction.assert_awaited_once_with("⏳", self.cog.bot.user)
        self.assertIn("too long", self.send.call_args.args[0])

    async def test_cancellation_clears_wait_without_reply(self):
        waiting = asyncio.Event()
        self.source.add_reaction.side_effect = lambda *args: waiting.set()

        async def request(on_wait):
            on_wait()
            await asyncio.Future()

        task = asyncio.create_task(self.cog._deliver_chat(request, self.send, self.message, source=self.source))
        await waiting.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.source.remove_reaction.assert_awaited_once_with("⏳", self.cog.bot.user)
        self.send.assert_not_awaited()

    async def test_reaction_permission_error_does_not_block_reply(self):
        self.source.add_reaction.side_effect = discord.Forbidden(NS(status=403, reason="Forbidden"), "Denied")
        self.source.remove_reaction.side_effect = self.source.add_reaction.side_effect

        async def request(on_wait):
            on_wait()
            await asyncio.sleep(.01)
            return MikoResult(True, "Done.", completed=True)

        await self.cog._deliver_chat(request, self.send, self.message, source=self.source)
        self.send.assert_awaited_once()

    async def test_slash_defers_then_marks_the_delivered_reply(self):
        self.cog.disabled_channels = set()
        self.cog.channel_levels = {}

        async def ask_direct(*args, on_wait, **kwargs):
            on_wait()
            return MikoResult(True, "Done.", completed=True)

        self.cog.chat_engine.ask_direct = ask_direct
        interaction = NS(id=52, guild=self.message.guild, channel=self.message.channel,
                         user=self.message.author, response=NS(defer=AsyncMock()),
                         followup=NS(send=self.send))
        await MikoChat.ask_miko_slash.callback(self.cog, interaction, "Please explain.")
        interaction.response.defer.assert_awaited_once_with(thinking=True)
        self.assertTrue(self.send.call_args.kwargs["wait"])
        self.post.add_reaction.assert_awaited_once_with("✅")

    async def test_cancelled_slash_clears_the_deferred_status(self):
        self.cog.disabled_channels = set()
        self.cog.channel_levels = {}
        self.cog.chat_engine.ask_direct = AsyncMock(side_effect=asyncio.CancelledError)
        interaction = NS(id=52, guild=self.message.guild, channel=self.message.channel,
                         user=self.message.author, response=NS(defer=AsyncMock()),
                         followup=NS(send=self.send), delete_original_response=AsyncMock())
        with self.assertRaises(asyncio.CancelledError):
            await MikoChat.ask_miko_slash.callback(self.cog, interaction, "Please explain.")
        interaction.delete_original_response.assert_awaited_once()
        self.send.assert_not_awaited()

    async def test_asset_delivery_avoids_same_picture_only_in_same_scope(self):
        result = MikoResult(True, "Congratulations.", emotion_id=3, attach_emotion=True)
        await self.cog._send_chat(self.send, result, (10, 20, 1))
        await self.cog._send_chat(self.send, result, (10, 20, 1))
        await self.cog._send_chat(self.send, result, (10, 20, 2))
        kwargs = [call.kwargs for call in self.send.call_args_list]
        self.assertEqual(kwargs[0]["file"].filename, "miko_03.webp")
        self.assertNotIn("file", kwargs[1])
        self.assertIn("file", kwargs[2])

    async def test_missing_attachment_permission_retries_with_text(self):
        self.send.side_effect = [discord.Forbidden(NS(status=403, reason="Forbidden"), "Denied"), self.post]
        result = MikoResult(True, "Congratulations.", emotion_id=3, attach_emotion=True)
        sent = await self.cog._send_chat(self.send, result, (10, 20, 1))
        self.assertIs(sent, self.post)
        self.assertEqual(self.send.await_count, 2)
        self.assertNotIn("file", self.send.call_args.kwargs)
        self.assertFalse(self.cog.recent_images)
