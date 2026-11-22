"""Stream one Discord speaker to a Foundry voice agent."""
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
FOUNDRY_SILENCE = bytes(4800)  # 100 ms of 24 kHz mono PCM.
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
    def __init__(self, user_id, playback, loop, enqueue):
        super().__init__()
        self.user_id = user_id
        self.playback = playback
        self.loop = loop
        self.enqueue = enqueue

    def wants_opus(self):
        return False

    def write(self, user, data):
        if user is not None and user.id == self.user_id and not self.playback.playing_reply():
            self.loop.call_soon_threadsafe(self.enqueue, data.pcm)

    def cleanup(self):
        pass


async def send_audio(connection, incoming, playback):
    rate_state = None
    while True:
        try:
            stereo = await asyncio.wait_for(incoming.get(), timeout=0.1)
        except TimeoutError:
            rate_state = None
            await connection.input_audio_buffer.append(audio=FOUNDRY_SILENCE)
            continue
        if playback.playing_reply():
            continue
        mono = audioop.tomono(stereo, 2, 0.5, 0.5)
        resampled, rate_state = audioop.ratecv(mono, 2, 1, 48000, 24000, rate_state)
        await connection.input_audio_buffer.append(audio=resampled)


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


async def run_voice(voice, user_id, config, ready):
    loop = asyncio.get_running_loop()
    playback = DiscordPlayback()
    incoming = asyncio.Queue(maxsize=50)
    stopped = asyncio.Event()

    def enqueue(pcm):
        if pcm and not incoming.full():
            incoming.put_nowait(pcm)

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
        voice.listen(SpeakerSink(user_id, playback, loop, enqueue), after=audio_stopped)
        workers = [
            asyncio.create_task(send_audio(connection, incoming, playback)),
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
