"""Regression coverage for overlong spoken replies."""
import unittest

from services.foundry_voice import (
    DISCORD_FRAME_BYTES,
    MAX_PLAYBACK_FRAMES,
    MAX_REPLY_PCM_BYTES,
    DiscordPlayback,
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
