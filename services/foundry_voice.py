"""Stream turn-based Discord audio to a Foundry voice agent."""
import asyncio
import audioop
import base64
import logging
import queue
import re
import time
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from uuid import uuid4

import aiohttp
import discord
from azure.ai.projects.aio import AIProjectClient
from azure.core.pipeline.transport import AioHttpTransport
from azure.identity.aio import ClientSecretCredential
from discord.ext import voice_recv

from services.fish_audio import SynthesisError, VOICE_ID
from services.miko_voice_reply import VOICE_INSTRUCTIONS, parse_reply

logger = logging.getLogger(__name__)

DISCORD_FRAME_BYTES = 3840  # 20 ms of 48 kHz, stereo, signed 16-bit PCM.
BATCH_SECONDS = 0.2
FRAMES_PER_BATCH = 10
FOUNDRY_BATCH_BYTES = 9600  # 200 ms of 24 kHz mono PCM.
TRAILING_SILENCE_BATCHES = 10  # Two seconds for Foundry's end-of-turn detection.
SPEAKER_PAUSE_SECONDS = 0.7
MAX_INPUT_FRAMES = 25
MAX_REPLY_SECONDS = 20
MAX_REPLY_PCM_BYTES = MAX_REPLY_SECONDS * 24000 * 2
MAX_PLAYBACK_FRAMES = MAX_REPLY_SECONDS * 50
INPUT_RMS = 110
INTERRUPT_FRAMES = 18
INTERRUPT_PAUSE_SECONDS = 0.25
FALLBACK_PREFIX = "miko-tts-"
SPEAKER_EVENT_PREFIX = "miko-speaker-"
SETUP_EVENT = "miko-fish-setup"
FOUNDRY_CONNECT_TIMEOUT = 10
FOUNDRY_OUTPUT_TIMEOUT = 7
VOICE_STARTUP_TIMEOUT = FOUNDRY_CONNECT_TIMEOUT * 2 + FOUNDRY_OUTPUT_TIMEOUT + 5


class DiscordPlayback(discord.AudioSource):
    def __init__(self):
        self.frames = queue.Queue(maxsize=MAX_PLAYBACK_FRAMES)
        self.partial = bytearray()
        self.rate_state = None
        self.reply_bytes = 0
        self.last_audio = float("-inf")
        self.first_frame = None

    def read(self):
        try:
            frame = self.frames.get_nowait()
        except queue.Empty:
            return bytes(DISCORD_FRAME_BYTES)
        self.last_audio = time.monotonic()
        if isinstance(frame, tuple):
            frame, first_frame = frame
            first_frame(self.last_audio)
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
            self.frames.put_nowait((frame, self.first_frame) if self.first_frame else frame)
            self.first_frame = None
        except queue.Full:
            return False
        return True

    def finish_reply(self):
        if self.partial:
            self.enqueue_frame(bytes(self.partial).ljust(DISCORD_FRAME_BYTES, b"\0"))
            self.partial.clear()
        self.rate_state = None
        self.reply_bytes = 0

    def clear(self):
        self.partial.clear()
        self.rate_state = None
        self.reply_bytes = 0
        self.first_frame = None
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                break


class SpeakerSink(voice_recv.AudioSink):
    def __init__(self, participants, playback, incoming, *, interrupt=None, interrupt_rms=950):
        super().__init__()
        self.participants = participants
        self.playback = playback
        self.incoming = incoming
        self.speaker = None
        self.last_frame = float("-inf")
        self.interrupt = interrupt
        self.interrupt_rms = interrupt_rms
        self.barge_in = []

    def wants_opus(self):
        return False

    def write(self, user, data):
        if user is None or getattr(user, "bot", False):
            return
        enrollment = self.participants.get(user.id)
        if enrollment is None or len(data.pcm) != DISCORD_FRAME_BYTES:
            return
        now = time.monotonic()
        if self.playback.playing_reply():
            if self.interrupt is None:
                return
            if audioop.rms(data.pcm, 2) < self.interrupt_rms:
                if self.barge_in and self.barge_in[-1][:2] == (user.id, enrollment):
                    self.barge_in.clear()
                return
            if self.barge_in and (
                self.barge_in[-1][:2] != (user.id, enrollment)
                or now - self.last_frame > INTERRUPT_PAUSE_SECONDS
            ):
                self.barge_in.clear()
            self.last_frame = now
            self.barge_in.append((user.id, enrollment, data.pcm))
            if len(self.barge_in) == INTERRUPT_FRAMES:
                self.interrupt(self.barge_in)
                self.barge_in = []
            return

        self.barge_in.clear()
        if (
            self.speaker is not None
            and self.speaker != (user.id, enrollment)
            and self.participants.get(self.speaker[0]) is self.speaker[1]
            and now - self.last_frame < SPEAKER_PAUSE_SECONDS
        ):
            return

        if audioop.rms(data.pcm, 2) >= INPUT_RMS:
            self.speaker = (user.id, enrollment)
            self.last_frame = now
        try:
            self.incoming.put_nowait((user.id, enrollment, data.pcm))
        except queue.Full:
            pass

    def cleanup(self):
        pass


async def send_audio(connection, incoming, playback, participants, output=None):
    loop = asyncio.get_running_loop()
    silence_batches = 0
    active_speaker = None
    committed_turn = 0
    aliases = {}
    while True:
        next_batch = loop.time() + BATCH_SECONDS
        frames = []
        new_alias = None
        if output and committed_turn != output.committed_turn:
            committed_turn = output.committed_turn
            active_speaker = None
            silence_batches = 0
        for _ in range(FRAMES_PER_BATCH):
            try:
                user_id, enrollment, pcm = incoming.get_nowait()
            except queue.Empty:
                break
            if playback.playing_reply() or participants.get(user_id) is not enrollment:
                continue
            if active_speaker is None and audioop.rms(pcm, 2) >= INPUT_RMS:
                active_speaker = (user_id, enrollment)
                if output:
                    output.skip()
                    output.suppress_until_input = False
                    if output.provider == "fish" and output.speaker_labels:
                        new_alias = aliases.setdefault(user_id, len(aliases) + 1)
            if active_speaker == (user_id, enrollment):
                frames.append(pcm)

        audio = b""
        if frames:
            mono = audioop.tomono(b"".join(frames), 2, 0.5, 0.5)
            audio, _ = audioop.ratecv(mono, 2, 1, 48000, 24000, None)
        if audio and audioop.rms(audio, 2) >= INPUT_RMS:
            if new_alias is not None:
                await connection.conversation.item.create(
                    item={
                        "type": "message", "role": "system",
                        "content": [{"type": "input_text", "text": f"Incoming Discord speaker: Speaker {new_alias}."}],
                    },
                    event_id=SPEAKER_EVENT_PREFIX + uuid4().hex,
                )
            speaker_id, enrollment = active_speaker
            if participants.get(speaker_id) is enrollment:
                silence_batches = TRAILING_SILENCE_BATCHES
                await connection.input_audio_buffer.append(audio=audio.ljust(FOUNDRY_BATCH_BYTES, b"\0"))
            elif not silence_batches:
                active_speaker = None
        elif silence_batches:
            silence_batches -= 1
            await connection.input_audio_buffer.append(audio=bytes(FOUNDRY_BATCH_BYTES))
            if not silence_batches:
                active_speaker = None
        else:
            active_speaker = None

        await asyncio.sleep(max(0, next_batch - loop.time()))


class FoundrySessionError(RuntimeError):
    def __init__(self, error, *, stage="foundry_stream"):
        self.stage = stage
        self.error_type = self.safe_identifier(event_field(error, "type"))
        self.error_code = self.safe_identifier(event_field(error, "code"))
        self.error_param = self.safe_identifier(event_field(error, "param"))
        super().__init__("Foundry reported a realtime session error.")

    @staticmethod
    def safe_identifier(value):
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
            return value
        return "unavailable"


def event_field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


class VoiceStartupError(RuntimeError):
    def __init__(self, stage, reason):
        self.stage = stage
        self.error_code = reason
        super().__init__("Miko could not start the selected voice provider.")


class FishOutputUnavailable(VoiceStartupError):
    def __init__(self, reason):
        super().__init__("foundry_output_update", reason)


async def wait_for_session(connection):
    try:
        while True:
            event = await connection.recv()
            kind = event_field(event, "type")
            if kind == "session.created":
                return event
            if kind == "error":
                raise FoundrySessionError(event_field(event, "error"), stage="foundry_session_created")
            logger.debug("Foundry startup received %s before session creation.", FoundrySessionError.safe_identifier(kind))
    except ConnectionResetError as exc:
        raise VoiceStartupError("foundry_session_created", "connection_closed") from exc


async def configure_output(connection, first_event):
    """Enable Fish only after Foundry confirms that it will send text."""
    session = event_field(first_event, "session")
    original = event_field(session, "instructions") or ""
    try:
        async with asyncio.timeout(FOUNDRY_OUTPUT_TIMEOUT):
            await connection.session.update(
                session={
                    "type": "realtime",
                    "output_modalities": ["text"],
                    "instructions": f"{original}\n{VOICE_INSTRUCTIONS}".strip(),
                },
                event_id=SETUP_EVENT,
            )
            while True:
                event = await connection.recv()
                match event_field(event, "type"):
                    case "session.updated":
                        settings = event_field(event, "session")
                        modes = event_field(settings, "output_modalities")
                        if modes is None:
                            modes = event_field(settings, "modalities")
                        if isinstance(modes, (list, tuple)) and list(modes) == ["text"]:
                            return
                        raise FishOutputUnavailable("text_mode_not_confirmed")
                    case "error":
                        error = event_field(event, "error")
                        if event_field(error, "event_id") not in (None, SETUP_EVENT):
                            raise FoundrySessionError(error, stage="foundry_output_update")
                        raise FishOutputUnavailable(FoundrySessionError.safe_identifier(event_field(error, "code")))
    except TimeoutError as exc:
        raise FishOutputUnavailable("update_timeout") from exc
    except (ConnectionError, aiohttp.ClientConnectionError) as exc:
        raise FishOutputUnavailable("connection_closed") from exc


@asynccontextmanager
async def open_foundry_voice(realtime, agent, output):
    """Reopen the original Azure mode once if Fish setup leaves session state uncertain."""
    while True:
        # Bound setup only. Once ready, this connection lives for the whole call.
        async with AsyncExitStack() as stack:
            try:
                async with asyncio.timeout(FOUNDRY_CONNECT_TIMEOUT):
                    connection = await stack.enter_async_context(realtime.connect(agent_name=agent))
                    first_event = await wait_for_session(connection)
            except TimeoutError as exc:
                raise VoiceStartupError("foundry_session_created", "startup_timeout") from exc
            if output.provider == "fish":
                try:
                    await configure_output(connection, first_event)
                except FishOutputUnavailable as exc:
                    output.provider = "azure"
                    output.setup_issue = exc.error_code
                    logger.warning(
                        "Foundry did not enable Fish Audio text mode (code=%s). Reopening with Azure voice.",
                        exc.error_code,
                    )
                    continue

            logger.info("Miko Foundry session ready (provider=%s).", output.provider)
            yield connection
            return


class VoiceOutput:
    """A single cancellable reply per guild, including time spent waiting for Fish."""
    def __init__(self, playback, fish, provider, voice_id):
        self.playback = playback
        self.fish = fish
        self.provider = provider
        self.setup_issue = None
        self.voice_id = voice_id
        self.generation = 0
        self.jobs = set()
        self.job = None
        self.phase = "idle"
        self.native_id = None
        self.fallback_event = None
        self.fallback_timer = None
        self.turn_stopped = None
        self.committed_turn = 0
        self.suppress_until_input = False
        self.speaker_labels = True
        self.fallback_allowed = True
        self.last_provider = None

    def skip(self):
        self.generation += 1
        if self.job and not self.job.done():
            self.job.cancel()
        self.job = None
        self.finish_fallback()
        self.native_id = None
        self.turn_stopped = None
        self.playback.clear()

    async def close(self):
        self.skip()
        await asyncio.gather(*self.jobs, return_exceptions=True)

    def prepare(self, requested):
        if requested == "fish" and VOICE_ID.fullmatch(self.voice_id) and self.fish.configured:
            self.provider = "fish"

    def play(self, connection, reply, requested):
        turn_stopped = self.turn_stopped
        self.skip()
        self.phase = "queued"
        self.job = asyncio.create_task(
            self._play(connection, reply, requested, self.generation, turn_stopped)
        )
        self.jobs.add(self.job)
        self.job.add_done_callback(self.jobs.discard)

    def first_frame(self, requested, turn_stopped):
        loop = asyncio.get_running_loop()

        def record():
            metrics = self.fish.metrics
            now = loop.time()
            metrics.playback_count += 1
            metrics.total_playback_start += now - requested
            if turn_stopped is not None:
                metrics.response_count += 1
                metrics.total_response_time += now - turn_stopped
            self.last_provider = "fish"
            logger.info("Fish Audio playback started seconds=%.2f", now - requested)

        return lambda _when: loop.call_soon_threadsafe(record)

    async def _play(self, connection, reply, requested, generation, turn_stopped):
        emitted = False
        try:
            async with asyncio.timeout(35):
                async with aclosing(self.fish.pcm(reply.speech_text, self.voice_id)) as chunks:
                    async for pcm in chunks:
                        if not emitted:
                            self.playback.first_frame = self.first_frame(requested, turn_stopped)
                            self.phase = "playing"
                        self.playback.append(pcm)
                        emitted = True
                self.playback.finish_reply()
        except (SynthesisError, TimeoutError):
            if emitted:
                self.playback.finish_reply()
            elif self.fallback_allowed and generation == self.generation:
                await self.azure_fallback(connection, reply.display_text)
        except Exception as exc:
            logger.warning("Miko speech job ended (%s).", type(exc).__name__)
        finally:
            if generation == self.generation and self.phase != "azure fallback":
                self.phase = "idle"

    def finish_fallback(self):
        if self.fallback_timer:
            self.fallback_timer.cancel()
            self.fallback_timer = None
        self.fallback_event = None
        self.phase = "idle"

    def fallback_timed_out(self):
        self.native_id = None
        self.playback.finish_reply()
        self.finish_fallback()
        logger.warning("Miko's Azure synthesis fallback timed out.")

    async def azure_fallback(self, connection, text):
        event_id = FALLBACK_PREFIX + uuid4().hex
        self.fallback_event = event_id
        self.phase = "azure fallback"
        self.fallback_timer = asyncio.get_running_loop().call_later(15, self.fallback_timed_out)
        try:
            await connection.response.create(
                response={
                    "conversation": "none",
                    "metadata": {"miko_fallback": event_id},
                    "output_modalities": ["audio"],
                    "pre_generated_assistant_message": {
                        "type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    },
                },
                event_id=event_id,
            )
        except Exception as exc:
            logger.warning("Miko Azure fallback could not start (%s).", type(exc).__name__)
            self.finish_fallback()


def response_text(response):
    texts = []
    for item in event_field(response, "output") or ():
        if event_field(item, "role") != "assistant":
            continue
        for part in event_field(item, "content") or ():
            text = event_field(part, "text")
            if event_field(part, "type") == "output_text" and isinstance(text, str):
                texts.append(text)
    return "\n".join(texts)


async def receive_audio(connection, output):
    loop = asyncio.get_running_loop()
    current_id = None
    current_generation = None
    created = None
    final_text = ""
    async for event in connection:
        kind = event_field(event, "type")
        response = event_field(event, "response")
        if kind == "input_audio_buffer.speech_stopped":
            output.turn_stopped = loop.time()
        elif kind == "input_audio_buffer.committed":
            output.committed_turn += 1
        elif kind == "response.created":
            response_id = event_field(response, "id")
            metadata = event_field(response, "metadata") or {}
            fallback = event_field(metadata, "miko_fallback")
            if fallback:
                if fallback == output.fallback_event:
                    output.native_id = response_id
                    output.last_provider = "azure"
                continue
            current_id = response_id
            current_generation = output.generation
            created = loop.time()
            final_text = ""
            if output.provider == "azure" and not output.suppress_until_input:
                output.native_id = response_id
                output.last_provider = "azure"
        elif kind == "response.output_text.done" and event_field(event, "response_id") == current_id:
            text = event_field(event, "text")
            if isinstance(text, str) and len(final_text) + len(text) <= 4096:
                final_text += text
        elif kind == "response.output_audio.delta" and output.native_id:
            if event_field(event, "response_id") == output.native_id:
                delta = event_field(event, "delta")
                try:
                    pcm = base64.b64decode(delta, validate=True) if isinstance(delta, str) else delta
                except ValueError:
                    logger.warning("Foundry sent invalid audio.")
                    continue
                if isinstance(pcm, bytes):
                    output.playback.append(pcm[:len(pcm) // 2 * 2])
        elif kind == "response.done":
            response_id = event_field(response, "id")
            if response_id and response_id == output.native_id:
                output.playback.finish_reply()
                output.native_id = None
                output.finish_fallback()
            if response_id != current_id or output.provider != "fish":
                continue
            if (
                output.suppress_until_input or current_generation != output.generation
                or event_field(response, "status") != "completed"
            ):
                continue
            reply = parse_reply(response_text(response) or final_text)
            now = loop.time()
            if created is not None:
                metrics = output.fish.metrics
                metrics.ai_count += 1
                metrics.total_ai_time += now - created
            if reply.should_speak:
                output.play(connection, reply, now)
            current_id = None
        elif kind == "error":
            error = event_field(event, "error")
            event_id = event_field(error, "event_id") or ""
            if isinstance(event_id, str) and event_id.startswith(FALLBACK_PREFIX):
                if output.fallback_event == event_id:
                    output.native_id = None
                    output.finish_fallback()
                output.fallback_allowed = False
                logger.warning("Foundry rejected the Azure fallback response (%s).", FoundrySessionError(error).error_code)
                continue
            if isinstance(event_id, str) and event_id.startswith(SPEAKER_EVENT_PREFIX):
                output.speaker_labels = False
                logger.warning("Foundry rejected speaker labels; continuing without them.")
                continue
            raise FoundrySessionError(error)


async def run_voice(voice, participants, config, ready, output, interrupt_rms, connected):
    loop = asyncio.get_running_loop()
    playback = output.playback
    incoming = queue.Queue(maxsize=MAX_INPUT_FRAMES)
    stopped = asyncio.Event()

    def audio_stopped(error):
        if error:
            logger.warning("Discord voice audio stopped (%s).", type(error).__name__)
        loop.call_soon_threadsafe(stopped.set)

    def interrupt_on_loop(frames):
        if not playback.playing_reply() or participants.get(frames[0][0]) is not frames[0][1]:
            return
        output.skip()
        playback.last_audio = float("-inf")
        for frame in frames:
            try:
                incoming.put_nowait(frame)
            except queue.Full:
                break

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
        open_foundry_voice(client.voice_agents.realtime, agent, output) as connection,
    ):
        voice.play(playback, after=audio_stopped)
        voice.listen(
            SpeakerSink(
                participants, playback, incoming,
                interrupt=lambda frames: loop.call_soon_threadsafe(interrupt_on_loop, frames),
                interrupt_rms=interrupt_rms,
            ),
            after=audio_stopped,
        )
        connected(connection)
        ready.set()
        workers = [
            asyncio.create_task(send_audio(connection, incoming, playback, participants, output)),
            asyncio.create_task(receive_audio(connection, output)),
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
            await output.close()
