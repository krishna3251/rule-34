"""Stream turn-based Discord audio to a Foundry voice agent."""
import asyncio
import audioop
import base64
import queue
import re
import time

import aiohttp
import discord
from azure.ai.projects.aio import AIProjectClient
from azure.core.pipeline.transport import AioHttpTransport
from azure.identity.aio import ClientSecretCredential
from discord.ext import voice_recv

DISCORD_FRAME_BYTES = 3840  # 20 ms of 48 kHz, stereo, signed 16-bit PCM.
BATCH_SECONDS = 0.1
FRAMES_PER_BATCH = 5
FOUNDRY_BATCH_BYTES = 4800  # 100 ms of 24 kHz mono PCM.
SPEAKER_PAUSE_SECONDS = 0.7
MAX_INPUT_FRAMES = 25
MAX_REPLY_SECONDS = 12
MAX_REPLY_PCM_BYTES = MAX_REPLY_SECONDS * 24000 * 2
MAX_PLAYBACK_FRAMES = MAX_REPLY_SECONDS * 50


class DiscordPlayback(discord.AudioSource):
    def __init__(self):
        self.frames = queue.Queue(maxsize=MAX_PLAYBACK_FRAMES)
        self.partial = bytearray()
        self.rate_state = None
        self.reply_bytes = 0
        self.last_audio = float("-inf")

    def read(self):
        try:
            frame = self.frames.get_nowait()
        except queue.Empty:
            return bytes(DISCORD_FRAME_BYTES)
        self.last_audio = time.monotonic()
        return frame

    def playing_reply(self):
        return not self.frames.empty() or time.monotonic() - self.last_audio < 0.25

    def append(self, mono):
        remaining = MAX_REPLY_PCM_BYTES - self.reply_bytes
        if remaining == 0:
            return
        mono = mono[:remaining]
        self.reply_bytes += len(mono)
        pcm, self.rate_state = audioop.ratecv(mono, 2, 1, 24000, 48000, self.rate_state)
        self.partial.extend(audioop.tostereo(pcm, 2, 1, 1))
        complete = len(self.partial) // DISCORD_FRAME_BYTES * DISCORD_FRAME_BYTES
        for offset in range(0, complete, DISCORD_FRAME_BYTES):
            if not self.enqueue_frame(bytes(self.partial[offset:offset + DISCORD_FRAME_BYTES])):
                break
        del self.partial[:complete]

    def enqueue_frame(self, frame):
        try:
            self.frames.put_nowait(frame)
        except queue.Full:
            return False
        return True

    def finish_reply(self):
        if self.partial:
            self.enqueue_frame(bytes(self.partial).ljust(DISCORD_FRAME_BYTES, b"\0"))
            self.partial.clear()
        self.rate_state = None
        self.reply_bytes = 0


class SpeakerSink(voice_recv.AudioSink):
    def __init__(self, participants, playback, incoming):
        super().__init__()
        self.participants = participants
        self.playback = playback
        self.incoming = incoming
        self.speaker = None
        self.last_frame = float("-inf")

    def wants_opus(self):
        return False

    def write(self, user, data):
        if user is None or self.playback.playing_reply():
            return
        enrollment = self.participants.get(user.id)
        if enrollment is None or len(data.pcm) != DISCORD_FRAME_BYTES:
            return
        now = time.monotonic()
        if (
            self.speaker is not None
            and self.speaker != (user.id, enrollment)
            and self.participants.get(self.speaker[0]) is self.speaker[1]
            and now - self.last_frame < SPEAKER_PAUSE_SECONDS
        ):
            return

        self.speaker = (user.id, enrollment)
        self.last_frame = now
        try:
            self.incoming.put_nowait((user.id, enrollment, data.pcm))
        except queue.Full:
            pass

    def cleanup(self):
        pass


async def send_audio(connection, incoming, playback, participants):
    loop = asyncio.get_running_loop()
    while True:
        next_batch = loop.time() + BATCH_SECONDS
        frames = []
        for _ in range(FRAMES_PER_BATCH):
            try:
                user_id, enrollment, pcm = incoming.get_nowait()
            except queue.Empty:
                break
            if not playback.playing_reply() and participants.get(user_id) is enrollment:
                frames.append(pcm)

        audio = b""
        if frames:
            mono = audioop.tomono(b"".join(frames), 2, 0.5, 0.5)
            audio, _ = audioop.ratecv(mono, 2, 1, 48000, 24000, None)
        await connection.input_audio_buffer.append(audio=audio.ljust(FOUNDRY_BATCH_BYTES, b"\0"))
        await asyncio.sleep(max(0, next_batch - loop.time()))


class FoundrySessionError(RuntimeError):
    def __init__(self, error):
        self.error_type = self.safe_identifier(getattr(error, "type", None))
        self.error_code = self.safe_identifier(getattr(error, "code", None))
        self.error_param = self.safe_identifier(getattr(error, "param", None))
        super().__init__("Foundry reported a realtime session error.")

    @staticmethod
    def safe_identifier(value):
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
            return value
        return "unavailable"


async def receive_audio(connection, playback, ready):
    async for event in connection:
        if event.type == "session.created":
            ready.set()
        elif event.type == "response.output_audio.delta":
            delta = event.delta
            playback.append(base64.b64decode(delta) if isinstance(delta, str) else delta)
        elif event.type == "response.done":
            playback.finish_reply()
        elif event.type == "error":
            raise FoundrySessionError(event.error)


async def run_voice(voice, participants, config, ready):
    loop = asyncio.get_running_loop()
    playback = DiscordPlayback()
    incoming = queue.Queue(maxsize=MAX_INPUT_FRAMES)
    stopped = asyncio.Event()

    def audio_stopped(_error):
        loop.call_soon_threadsafe(stopped.set)

    endpoint, agent, tenant_id, client_id, secret = config
    async with (
        ClientSecretCredential(tenant_id, client_id, secret) as credential,
        aiohttp.ClientSession(auto_decompress=False, headers={"Accept-Encoding": "gzip, deflate"}) as http,
        AIProjectClient(
            endpoint=endpoint,
            credential=credential,
            allow_preview=True,
            transport=AioHttpTransport(session=http, session_owner=False),
        ) as client,
        client.voice_agents.realtime.connect(agent_name=agent) as connection,
    ):
        voice.play(playback, after=audio_stopped)
        voice.listen(SpeakerSink(participants, playback, incoming), after=audio_stopped)
        workers = [
            asyncio.create_task(send_audio(connection, incoming, playback, participants)),
            asyncio.create_task(receive_audio(connection, playback, ready)),
            asyncio.create_task(stopped.wait()),
        ]
        try:
            done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
