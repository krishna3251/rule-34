"""Regression coverage for Discord voice playback and group input."""
import asyncio
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from services.foundry_voice import (
    DISCORD_FRAME_BYTES,
    FOUNDRY_BATCH_BYTES,
    FRAMES_PER_BATCH,
    INTERRUPT_FRAMES,
    MAX_PLAYBACK_FRAMES,
    MAX_REPLY_PCM_BYTES,
    TRAILING_SILENCE_BATCHES,
    DiscordPlayback,
    SpeakerSink,
    send_audio,
)


class DiscordPlaybackTests(unittest.TestCase):
    def test_caps_one_reply_even_when_discord_drains_frames(self):
        playback = DiscordPlayback()
        playback.append(b"\x01\x00" * (MAX_REPLY_PCM_BYTES // 2))
        while not playback.frames.empty():
            playback.read()

        playback.append(b"\x01\x00" * 480)
        self.assertTrue(playback.frames.empty())

        playback.finish_reply()
        self.assertNotEqual(playback.read(), bytes(DISCORD_FRAME_BYTES))
        playback.append(b"\x02\x00" * 480)
        playback.finish_reply()
        self.assertNotEqual(playback.read(), bytes(DISCORD_FRAME_BYTES))

    def test_full_queue_drops_extra_audio_and_keeps_playing(self):
        playback = DiscordPlayback()
        for _ in range(MAX_PLAYBACK_FRAMES):
            playback.frames.put_nowait(bytes(DISCORD_FRAME_BYTES))

        playback.append(b"\x01\x00" * 480)
        playback.finish_reply()
        self.assertEqual(playback.frames.qsize(), MAX_PLAYBACK_FRAMES)

        playback.read()
        playback.append(b"\x02\x00" * 480)
        playback.finish_reply()
        self.assertEqual(playback.frames.qsize(), MAX_PLAYBACK_FRAMES)


class SpeakerSinkTests(unittest.TestCase):
    def test_ignores_unenrolled_and_overlapping_speakers(self):
        participants = {1: object(), 2: object()}
        incoming = queue.Queue(maxsize=2)
        sink = SpeakerSink(participants, DiscordPlayback(), incoming)
        data = SimpleNamespace(pcm=b"\x00\x04" * (DISCORD_FRAME_BYTES // 2))

        with patch("services.foundry_voice.time.monotonic", return_value=0) as clock:
            sink.write(SimpleNamespace(id=3), data)
            self.assertTrue(incoming.empty())

            sink.write(SimpleNamespace(id=1), data)
            sink.write(SimpleNamespace(id=2), data)
            self.assertEqual(incoming.get_nowait()[0], 1)
            self.assertTrue(incoming.empty())

            clock.return_value = 1
            sink.write(SimpleNamespace(id=2), data)
            self.assertEqual(incoming.get_nowait()[0], 2)

    def test_interrupt_requires_sustained_enrolled_human_speech(self):
        playback = DiscordPlayback()
        playback.enqueue_frame(bytes(DISCORD_FRAME_BYTES))
        incoming = queue.Queue()
        interrupt = Mock()
        sink = SpeakerSink({1: object(), 2: object()}, playback, incoming, interrupt=interrupt)
        loud = SimpleNamespace(pcm=b"\x00\x08" * (DISCORD_FRAME_BYTES // 2))
        quiet = SimpleNamespace(pcm=bytes(DISCORD_FRAME_BYTES))
        user = SimpleNamespace(id=1)
        bot = SimpleNamespace(id=2, bot=True)

        for _ in range(INTERRUPT_FRAMES):
            sink.write(bot, loud)
            sink.write(user, loud)
            sink.write(user, quiet)
        interrupt.assert_not_called()
        self.assertTrue(incoming.empty())

        for _ in range(INTERRUPT_FRAMES):
            sink.write(user, loud)
        interrupt.assert_called_once()
        self.assertEqual(len(interrupt.call_args.args[0]), INTERRUPT_FRAMES)

    def test_full_input_queue_drops_frames_without_throwing(self):
        incoming = queue.Queue(maxsize=1)
        sink = SpeakerSink({1: object()}, DiscordPlayback(), incoming)
        user = SimpleNamespace(id=1)
        data = SimpleNamespace(pcm=b"\x00\x04" * (DISCORD_FRAME_BYTES // 2))
        sink.write(user, data)
        sink.write(user, data)
        self.assertEqual(incoming.qsize(), 1)


class FoundryInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_batches_ten_frames_in_one_request(self):
        enrollment = object()
        incoming = queue.Queue()
        for _ in range(FRAMES_PER_BATCH):
            incoming.put_nowait((1, enrollment, b"\x00\x04" * (DISCORD_FRAME_BYTES // 2)))
        append = AsyncMock(side_effect=asyncio.CancelledError)
        connection = SimpleNamespace(input_audio_buffer=SimpleNamespace(append=append))
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(send_audio(connection, incoming, DiscordPlayback(), {1: enrollment}), 2)
        append.assert_awaited_once_with(audio=b"\x00\x04" * (FOUNDRY_BATCH_BYTES // 2))
        self.assertTrue(incoming.empty())

    async def test_optout_discards_audio_queued_before_revocation(self):
        incoming = queue.Queue()
        incoming.put_nowait((1, object(), b"\x00\x04" * (DISCORD_FRAME_BYTES // 2)))
        append = AsyncMock()
        connection = SimpleNamespace(input_audio_buffer=SimpleNamespace(append=append))
        with patch("services.foundry_voice.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await send_audio(connection, incoming, DiscordPlayback(), {1: object()})
        append.assert_not_awaited()
        self.assertTrue(incoming.empty())

    async def test_optout_during_speaker_label_send_drops_audio(self):
        participants = {17: object()}
        incoming = queue.Queue()
        incoming.put_nowait((17, participants[17], b"\x00\x04" * (DISCORD_FRAME_BYTES // 2)))

        async def revoke(**kwargs):
            participants.clear()

        append = AsyncMock()
        label = AsyncMock(side_effect=revoke)
        connection = SimpleNamespace(
            input_audio_buffer=SimpleNamespace(append=append),
            conversation=SimpleNamespace(item=SimpleNamespace(create=label)),
        )
        output = SimpleNamespace(committed_turn=0, provider="fish", speaker_labels=True, skip=Mock())
        with patch("services.foundry_voice.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await send_audio(connection, incoming, DiscordPlayback(), participants, output)
        append.assert_not_awaited()
        self.assertEqual(label.await_args.kwargs["item"]["content"][0]["text"], "Incoming Discord speaker: Speaker 1.")

    async def test_stream_pauses_after_bounded_trailing_silence(self):
        enrollment = object()
        incoming = queue.Queue()
        incoming.put_nowait((1, enrollment, b"\x00\x04" * (DISCORD_FRAME_BYTES // 2)))
        append = AsyncMock()
        connection = SimpleNamespace(input_audio_buffer=SimpleNamespace(append=append))

        # Run through the two-second tail and several idle intervals without waiting.
        pauses = [None] * (TRAILING_SILENCE_BATCHES + 4) + [asyncio.CancelledError]
        with patch("services.foundry_voice.asyncio.sleep", side_effect=pauses):
            with self.assertRaises(asyncio.CancelledError):
                await send_audio(connection, incoming, DiscordPlayback(), {1: enrollment})

        payloads = [call.kwargs["audio"] for call in append.await_args_list]
        self.assertNotEqual(payloads[0], bytes(FOUNDRY_BATCH_BYTES))
        self.assertEqual(payloads[1:], [bytes(FOUNDRY_BATCH_BYTES)] * TRAILING_SILENCE_BATCHES)

    async def test_idle_silent_frames_do_not_send_messages(self):
        enrollment = object()
        incoming = queue.Queue()
        incoming.put_nowait((1, enrollment, bytes(DISCORD_FRAME_BYTES)))
        append = AsyncMock()
        connection = SimpleNamespace(input_audio_buffer=SimpleNamespace(append=append))
        with patch("services.foundry_voice.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await send_audio(connection, incoming, DiscordPlayback(), {1: enrollment})
        append.assert_not_awaited()
