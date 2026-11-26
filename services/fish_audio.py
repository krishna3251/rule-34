"""Fish Audio speech synthesis with bounded, incremental MP3 decoding."""
import asyncio
import logging
import os
import re
import shutil
from collections import OrderedDict
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

import aiohttp

logger = logging.getLogger(__name__)

MODEL = "s2.1-pro-free"
DEFAULT_VOICE = "b2d9d8db057042688a5e318b8f405bc2"
ENDPOINT = "https://api.fish.audio/v1/tts"
MAX_MP3_BYTES = 1024 * 1024
MAX_PCM_BYTES = 20 * 24000 * 2
MAX_RETRY_WAIT = 2
VOICE_ID = re.compile(r"[a-fA-F0-9]{32}")


class SynthesisError(RuntimeError):
    def __init__(self, reason, *, transient=False, retry_after=None, reference_specific=False):
        self.reason = reason
        self.transient = transient
        self.retry_after = retry_after
        self.reference_specific = reference_specific
        super().__init__(reason)


@dataclass
class FishMetrics:
    success: int = 0
    failures: int = 0
    last_success: datetime | None = None
    total_synthesis: float = 0
    playback_count: int = 0
    total_playback_start: float = 0
    ai_count: int = 0
    total_ai_time: float = 0
    queue_count: int = 0
    total_queue_time: float = 0
    response_count: int = 0
    total_response_time: float = 0
    last_error: str = ""


def retry_after_seconds(value):
    if not value:
        return None
    try:
        delay = Decimal(value)
        if delay.is_finite():
            return float(min(max(delay, 0), 86400))
    except InvalidOperation:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is not None:
                return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, OverflowError):
            pass
    return None


def response_error(status, retry_after):
    reason = {
        400: "request rejected (model, reference voice, or text)",
        401: "authentication rejected",
        402: "free model unavailable or credit required",
        403: "model or reference voice access denied",
        404: "model or reference voice unavailable",
        422: "request rejected (model, reference voice, or text)",
        429: "rate limited",
    }.get(status, f"HTTP {status}")
    return SynthesisError(
        reason, transient=status in {408, 429} or 500 <= status < 600,
        retry_after=retry_after_seconds(retry_after),
        reference_specific=status in {400, 403, 404, 422},
    )


class FishAudio:
    """One connection pool and one concurrent synthesis across all guilds."""

    def __init__(self):
        self._key = os.getenv("FISH_AUDIO_API_KEY", "").strip()
        self.ffmpeg = shutil.which("ffmpeg")
        self.voice_id = os.getenv("FISH_AUDIO_REFERENCE_ID", DEFAULT_VOICE).strip()
        self.metrics = FishMetrics()
        self._http = None
        self._lock = asyncio.Lock()
        self._cooldown_until = 0
        self._failures = 0
        self._reference_cooldowns = OrderedDict()
        self._closed = False

    @property
    def configured(self):
        return bool(self._key and self.ffmpeg and not self._closed)

    def unavailable(self, voice_id=None):
        if self._closed:
            return "provider closed"
        if not self._key:
            return "API key missing"
        if not self.ffmpeg:
            return "FFmpeg unavailable"
        now = asyncio.get_running_loop().time()
        if now < self._cooldown_until:
            return "cooldown active"
        if voice_id and now < self._reference_cooldowns.get(voice_id, 0):
            return "reference voice cooldown active"
        return ""

    def reset(self):
        self._cooldown_until = 0
        self._failures = 0
        self._reference_cooldowns.clear()
        self.metrics.last_error = ""

    async def close(self):
        self._closed = True
        if self._http is not None:
            await self._http.close()

    async def pcm(self, text, voice_id):
        """Yield 24 kHz mono PCM. Retry only before emitting any PCM."""
        if not VOICE_ID.fullmatch(voice_id):
            raise SynthesisError("invalid reference voice ID")
        loop = asyncio.get_running_loop()
        queued = loop.time()
        async with self._lock:
            if unavailable := self.unavailable(voice_id):
                raise SynthesisError(unavailable)
            self.metrics.queue_count += 1
            self.metrics.total_queue_time += loop.time() - queued
            if self._http is None:
                self._http = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=15, sock_connect=4, sock_read=7),
                    connector=aiohttp.TCPConnector(limit=1),
                )
            started = loop.time()
            for attempt in range(2):
                emitted = False
                try:
                    async with aclosing(self._request(text, voice_id, buffered=bool(attempt))) as chunks:
                        async for pcm in chunks:
                            emitted = True
                            yield pcm
                except SynthesisError as exc:
                    delay = exc.retry_after if exc.retry_after is not None else 0.5 * 2 ** attempt
                    if exc.transient and not emitted and attempt == 0 and delay <= MAX_RETRY_WAIT:
                        logger.info("Fish Audio retry reason=%s delay=%.2fs", exc.reason, delay)
                        await asyncio.sleep(delay)
                        continue
                    self.metrics.failures += 1
                    self.metrics.last_error = exc.reason
                    if exc.reference_specific:
                        self._reference_cooldowns[voice_id] = loop.time() + 300
                        self._reference_cooldowns.move_to_end(voice_id)
                        if len(self._reference_cooldowns) > 64:
                            self._reference_cooldowns.popitem(last=False)
                    else:
                        self._failures += 1
                        if exc.retry_after is not None or self._failures >= 2 or not exc.transient:
                            cooldown = exc.retry_after or (60 if exc.transient else 300)
                            self._cooldown_until = loop.time() + max(1, cooldown)
                    logger.warning("Fish Audio synthesis failed reason=%s", exc.reason)
                    raise
                else:
                    duration = loop.time() - started
                    self._failures = 0
                    self._reference_cooldowns.pop(voice_id, None)
                    self.metrics.success += 1
                    self.metrics.total_synthesis += duration
                    self.metrics.last_success = datetime.now(timezone.utc)
                    logger.info("Fish Audio synthesis succeeded seconds=%.2f", duration)
                    return

    async def _request(self, text, voice_id, *, buffered):
        async def mp3_chunks(response):
            received = 0
            buffer = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                received += len(chunk)
                if received > MAX_MP3_BYTES:
                    raise SynthesisError("audio exceeded the size limit")
                if buffered:
                    buffer.extend(chunk)
                else:
                    yield chunk
            if not received:
                raise SynthesisError("empty audio", transient=True)
            if buffered:
                yield bytes(buffer)

        try:
            async with asyncio.timeout(18):
                async with self._http.post(
                    ENDPOINT,
                    headers={"Authorization": f"Bearer {self._key}", "model": MODEL},
                    json={"text": text, "reference_id": voice_id, "format": "mp3", "latency": "low"},
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise response_error(response.status, response.headers.get("Retry-After"))
                    if response.content_type not in {"audio/mpeg", "audio/mp3", "application/octet-stream"}:
                        raise SynthesisError("unexpected audio content type", transient=True)
                    async with aclosing(self._decode(mp3_chunks(response))) as chunks:
                        async for pcm in chunks:
                            yield pcm
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise SynthesisError("network error or timeout", transient=True) from exc

    async def _decode(self, mp3):
        try:
            process = await asyncio.create_subprocess_exec(
                self.ffmpeg, "-hide_banner", "-loglevel", "error", "-xerror",
                "-f", "mp3", "-probesize", "4096", "-analyzeduration", "0", "-i", "pipe:0",
                "-f", "s16le", "-ac", "1", "-ar", "24000", "-t", "20", "pipe:1",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=8192,
            )
        except OSError as exc:
            raise SynthesisError("FFmpeg could not start") from exc

        async def feed():
            try:
                async for chunk in mp3:
                    process.stdin.write(chunk)
                    await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        writer = asyncio.create_task(feed())
        received = 0
        try:
            # Whole 16-bit samples, including when a pipe read ends at an odd byte.
            while chunk := await process.stdout.read(4096):
                received += len(chunk)
                if received > MAX_PCM_BYTES:
                    raise SynthesisError("decoded audio exceeded the size limit")
                if len(chunk) % 2:
                    try:
                        chunk += await process.stdout.readexactly(1)
                    except asyncio.IncompleteReadError as exc:
                        raise SynthesisError("invalid PCM") from exc
                    received += 1
                yield chunk
            await writer
            if await process.wait() != 0 or not received:
                raise SynthesisError("invalid or empty MP3", transient=True)
        finally:
            writer.cancel()
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await asyncio.gather(writer, return_exceptions=True)
            # Drain the pipe before waiting so a cancelled decode cannot retain FFmpeg.
            await process.stdout.read()
            await process.wait()
