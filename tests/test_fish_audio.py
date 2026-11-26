"""Regression tests for Fish synthesis, reply validation, and Foundry routing."""
import asyncio
import base64
import json
import shutil
import unittest
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web

from services.fish_audio import DEFAULT_VOICE, MODEL, FishAudio, SynthesisError, response_error
from services.foundry_voice import (
    DISCORD_FRAME_BYTES,
    DiscordPlayback,
    VoiceOutput,
    configure_output,
    receive_audio,
)
from services.miko_voice_reply import parse_reply


class VoiceReplyTests(unittest.TestCase):
    def test_keeps_only_matching_words_and_allowed_cues(self):
        reply = parse_reply(json.dumps({
            "display_text": "Arre, तुम जीत गए!",
            "speech_text": "[excited] Arre, [unsupported] तुम जीत गए!",
            "emotion": "excited",
            "should_speak": True,
        }))
        self.assertEqual(reply.display_text, "Arre, तुम जीत गए!")
        self.assertEqual(reply.speech_text, "[excited] Arre, तुम जीत गए!")
        self.assertEqual(reply.emotion, "excited")
        self.assertEqual(
            parse_reply('{"display_text":"Well done!","speech_text":"Say something else"}').speech_text,
            "Well done!",
        )

    def test_silence_malformed_json_and_plain_text(self):
        for raw in (
            "", '{"should_speak":false,"display_text":"Ignore the flag"}',
            '{"should_speak":"false","display_text":"Ignore the flag"}',
            '{"speech_text":', '{"speech_text":[]}', "[]",
        ):
            with self.subTest(raw=raw):
                self.assertFalse(parse_reply(raw).should_speak)
        self.assertEqual(parse_reply("[softly] Are you okay?").speech_text, "[soft tone] Are you okay?")


class FishRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch.dict("os.environ", {"FISH_AUDIO_API_KEY": "test-token", "FISH_AUDIO_REFERENCE_ID": DEFAULT_VOICE}):
            self.fish = FishAudio()
        self.fish.ffmpeg = "test-ffmpeg"
        self.addAsyncCleanup(self.fish.close)

    async def consume(self, voice=DEFAULT_VOICE):
        async with aclosing(self.fish.pcm("Test", voice)) as chunks:
            return b"".join([chunk async for chunk in chunks])

    async def test_transient_retry_uses_buffered_mode(self):
        modes = []

        async def request(text, voice, *, buffered):
            modes.append(buffered)
            if not buffered:
                raise SynthesisError("network error", transient=True, retry_after=0)
            yield b"\x01\x00"

        with patch.object(self.fish, "_request", request):
            self.assertEqual(await self.consume(), b"\x01\x00")
        self.assertEqual(modes, [False, True])
        self.assertEqual(self.fish.metrics.success, 1)

    async def test_rate_limit_and_auth_stop_requests(self):
        for status, delay in ((429, "90"), (401, None), (402, None)):
            with self.subTest(status=status):
                self.fish.reset()
                calls = []

                async def request(text, voice, *, buffered):
                    calls.append(voice)
                    raise response_error(status, delay)
                    yield  # Keep the same asynchronous iterator contract.

                with patch.object(self.fish, "_request", request):
                    for _ in range(2):
                        with self.assertRaises(SynthesisError):
                            await self.consume()
                self.assertEqual(calls, [DEFAULT_VOICE])
                self.assertEqual(self.fish.unavailable(DEFAULT_VOICE), "cooldown active")

    async def test_voice_rejection_does_not_disable_other_voice(self):
        async def request(text, voice, *, buffered):
            if voice == DEFAULT_VOICE:
                raise response_error(404, None)
            yield b"\x01\x00"

        with patch.object(self.fish, "_request", request):
            with self.assertRaises(SynthesisError):
                await self.consume()
            self.assertTrue(self.fish.unavailable(DEFAULT_VOICE))
            self.assertEqual(await self.consume("1" * 32), b"\x01\x00")
            self.assertTrue(self.fish.unavailable(DEFAULT_VOICE))

    async def test_concurrent_guilds_cannot_start_parallel_requests(self):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def request(text, voice, *, buffered):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            yield b"\x01\x00"

        with patch.object(self.fish, "_request", request):
            first = asyncio.create_task(self.consume())
            await asyncio.wait_for(started.wait(), 1)
            second = asyncio.create_task(self.consume())
            try:
                await asyncio.sleep(0)
                self.assertEqual(calls, 1)
            finally:
                release.set()
                await asyncio.gather(first, second)
        self.assertEqual(calls, 2)


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed for audio streaming")
class FishStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_mp3_before_full_response_and_closes_decoder(self):
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=6", "-b:a", "96k", "-f", "mp3", "pipe:1",
            stdout=asyncio.subprocess.PIPE,
        )
        mp3, _ = await process.communicate()
        self.assertEqual(process.returncode, 0)
        first_pcm = asyncio.Event()
        captured = {}

        async def tts(request):
            captured.update(await request.json())
            captured["model"] = request.headers.get("model")
            captured["auth"] = request.headers.get("authorization")
            response = web.StreamResponse(headers={"Content-Type": "audio/mpeg"})
            await response.prepare(request)
            await response.write(mp3[:len(mp3) // 2])
            await asyncio.wait_for(first_pcm.wait(), 4)
            await response.write(mp3[len(mp3) // 2:])
            return response

        app = web.Application()
        app.router.add_post("/v1/tts", tts)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]

        with patch.dict("os.environ", {"FISH_AUDIO_API_KEY": "test-token"}):
            fish = FishAudio()
        self.addAsyncCleanup(fish.close)
        with patch("services.fish_audio.ENDPOINT", f"http://127.0.0.1:{port}/v1/tts"):
            async with aclosing(fish.pcm("[excited] Hey!", DEFAULT_VOICE)) as stream:
                async with asyncio.timeout(6):
                    first = await anext(stream)
                    self.assertGreater(len(first), 0)
                    first_pcm.set()
                    total = len(first) + sum([len(part) async for part in stream])

        self.assertGreater(total, 24000 * 2 * 5)
        self.assertEqual(fish.metrics.success, 1)
        self.assertEqual(captured, {
            "model": MODEL, "auth": "Bearer test-token", "text": "[excited] Hey!",
            "reference_id": DEFAULT_VOICE, "format": "mp3", "latency": "low",
        })

    async def test_cancelled_decoder_releases_process(self):
        fish = FishAudio()
        never = asyncio.Event()
        processes = []
        original = asyncio.create_subprocess_exec

        async def blocked_mp3():
            yield b"invalid MP3"
            await never.wait()

        async def tracked_process(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            return process

        async def decode():
            async with aclosing(fish._decode(blocked_mp3())) as stream:
                await anext(stream)

        with patch("services.fish_audio.asyncio.create_subprocess_exec", tracked_process):
            task = asyncio.create_task(decode())
            async with asyncio.timeout(3):
                while not processes:
                    await asyncio.sleep(0.01)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertIsNotNone(processes[0].returncode)


class StubFoundry:
    def __init__(self, events=()):
        self.events = events
        self.session = SimpleNamespace(update=AsyncMock())
        self.response = SimpleNamespace(create=AsyncMock())
        self.recv = AsyncMock()

    async def __aiter__(self):
        for event in self.events:
            yield event


class FoundryRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.output = VoiceOutput(DiscordPlayback(), FishAudio(), "fish", DEFAULT_VOICE)
        self.addAsyncCleanup(self.output.close)

    async def test_only_enables_speech_after_text_only_ack(self):
        conn = StubFoundry()
        conn.recv.return_value = {"type": "session.updated", "session": {"output_modalities": ["text"]}}
        await configure_output(conn, self.output, {"session": {"instructions": "Existing rules."}})
        config = conn.session.update.await_args.kwargs["session"]
        self.assertEqual(config["output_modalities"], ["text"])
        self.assertTrue(config["instructions"].startswith("Existing rules."))
        self.assertEqual(self.output.provider, "fish")

        conn.recv.return_value = {"type": "error"}
        await configure_output(conn, self.output, {})
        self.assertEqual(self.output.provider, "azure")

    async def test_completed_text_invokes_fish_once_and_discards_azure_audio(self):
        spoken = []

        async def pcm(text, voice):
            spoken.append((text, voice))
            yield b"\x00\x04" * 480

        conn = StubFoundry([
            {"type": "response.created", "response": {"id": "first"}},
            {"type": "response.output_audio.delta", "response_id": "first", "delta": "BAAEAA=="},
            {"type": "response.output_text.done", "response_id": "first", "text": "[playful] You did it."},
            {"type": "response.done", "response": {"id": "first", "status": "completed"}},
            {"type": "response.done", "response": {"id": "first", "status": "completed"}},
        ])
        with patch.object(self.output.fish, "pcm", pcm):
            await receive_audio(conn, self.output)
            await self.output.job
        self.assertEqual(spoken, [("[playful] You did it.", DEFAULT_VOICE)])
        self.assertEqual(self.output.playback.frames.qsize(), 1)
        self.assertNotEqual(self.output.playback.read(), bytes(DISCORD_FRAME_BYTES))
        await asyncio.sleep(0)
        self.assertEqual(self.output.fish.metrics.playback_count, 1)
        conn.response.create.assert_not_awaited()

    async def test_fallback_plays_only_matching_azure_response(self):
        async def unavailable(text, voice):
            raise SynthesisError("rate limited")
            yield

        conn = StubFoundry()
        with patch.object(self.output.fish, "pcm", unavailable):
            self.output.play(conn, parse_reply("[playful] Hello!"), asyncio.get_running_loop().time())
            await self.output.job
        fallback = conn.response.create.await_args.kwargs["response"]
        self.assertEqual(fallback["output_modalities"], ["audio"])
        self.assertEqual(fallback["conversation"], "none")
        self.assertEqual(fallback["pre_generated_assistant_message"]["content"][0]["text"], "Hello!")
        conn.events = [
            {"type": "response.created", "response": {"id": "native", "metadata": fallback["metadata"]}},
            {"type": "response.output_audio.delta", "response_id": "extra", "delta": "BAAEAA=="},
            {"type": "response.output_audio.delta", "response_id": "native",
             "delta": base64.b64encode(b"\x00\x04" * 480).decode()},
            {"type": "response.done", "response": {"id": "native", "status": "completed"}},
        ]
        await receive_audio(conn, self.output)
        self.assertEqual(self.output.playback.frames.qsize(), 1)
        self.assertEqual(self.output.last_provider, "azure")
        self.assertIsNone(self.output.fallback_event)

    async def test_stop_cancels_synthesis_and_discards_already_buffered_audio(self):
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def pcm(text, voice):
            try:
                yield b"\x00\x04" * 960
                started.set()
                await asyncio.Future()
            finally:
                stopped.set()

        conn = StubFoundry()
        with patch.object(self.output.fish, "pcm", pcm):
            self.output.play(conn, parse_reply("Hello!"), asyncio.get_running_loop().time())
            await asyncio.wait_for(started.wait(), 1)
            self.assertTrue(self.output.playback.playing_reply())
            await self.output.close()
        self.assertTrue(stopped.is_set())
        self.assertTrue(self.output.playback.frames.empty())
        conn.response.create.assert_not_awaited()
