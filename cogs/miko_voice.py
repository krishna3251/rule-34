"""Opt-in Discord voice commands for Miko."""
import asyncio
import json
import logging
import os
from dataclasses import dataclass, field

from services.fish_audio import FishAudio, MODEL, VOICE_ID
from services.miko_voice_reply import DIRECTIONS, parse_reply

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

CONFIG_KEYS = (
    "FOUNDRY_PROJECT_ENDPOINT",
    "FOUNDRY_VOICE_AGENT_NAME",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
)


@dataclass
class VoiceSession:
    owner_id: int
    channel_id: int
    participants: dict[int, object] = field(default_factory=dict)
    task: asyncio.Task | None = None
    output: object = None
    connection: object = None


class MikoVoice(commands.Cog):
    miko = app_commands.Group(name="miko", description="Control Miko's live voice")
    tts = app_commands.Group(name="tts", description="Configure and test Miko's speech")
    voice = app_commands.Group(name="voice", description="Inspect or stop Miko's voice playback")

    def __init__(self, bot):
        self.bot = bot
        self.sessions = {}
        self.fish = FishAudio()
        self.providers = {}
        self.voices = {}
        try:
            self.interrupt_rms = min(32767, max(300, int(os.getenv("MIKO_INTERRUPT_RMS", "950"))))
        except ValueError:
            self.interrupt_rms = 950

    async def cog_unload(self):
        tasks = [session.task for session in self.sessions.values() if session.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.fish.close()

    async def run_session(self, interaction, channel, config, session):
        ready = asyncio.Event()
        voice = None
        bridge = None
        started = False
        try:
            from discord.ext.voice_recv import VoiceRecvClient
            from services.foundry_voice import DiscordPlayback, VoiceOutput, run_voice

            output = VoiceOutput(
                DiscordPlayback(), self.fish, "azure",
                self.voices.get(interaction.guild_id, self.fish.voice_id),
            )
            output.prepare(self.providers.get(interaction.guild_id, "fish"))
            session.output = output
            voice = await channel.connect(cls=VoiceRecvClient, timeout=15, reconnect=False, self_deaf=False)
            await channel.send(
                "Miko voice session started. Your mic is off for Miko unless you use **/miko optin**. "
                "Azure Foundry processes opted-in speech. If Fish Audio is enabled, it receives Miko's "
                "reply text for speech synthesis. Use **/miko optout** to stop sharing new mic audio.",
                allowed_mentions=discord.AllowedMentions.none(),
            )

            def connected(connection):
                session.connection = connection

            bridge = asyncio.create_task(run_voice(
                voice, session.participants, config, ready, output, self.interrupt_rms, connected,
            ))
            wait_ready = asyncio.create_task(ready.wait())
            try:
                done, _ = await asyncio.wait(
                    (bridge, wait_ready), timeout=20, return_when=asyncio.FIRST_COMPLETED,
                )
                if bridge in done:
                    bridge.result()
                    raise RuntimeError("The Foundry voice stream closed during setup.")
                if not ready.is_set():
                    raise RuntimeError("The Foundry voice stream did not start.")
            finally:
                wait_ready.cancel()
                await asyncio.gather(wait_ready, return_exceptions=True)

            await interaction.followup.send(
                f"Miko is ready. Voice provider: {'Fish Audio' if output.provider == 'fish' else 'Azure'}. "
                "Use /miko optin to speak and give each person time to finish. Use /miko leave to end the session.",
                ephemeral=True,
            )
            started = True
            try:
                await asyncio.wait_for(bridge, timeout=15 * 60)
            except TimeoutError:
                await interaction.followup.send("Miko's 15-minute voice session ended.", ephemeral=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Miko voice failed (%s, status=%s, foundry_type=%s, foundry_code=%s, foundry_param=%s, session_ready=%s).",
                type(exc).__name__,
                getattr(exc, "status_code", None),
                getattr(exc, "error_type", None),
                getattr(exc, "error_code", None),
                getattr(exc, "error_param", None),
                ready.is_set(),
            )
            message = "Miko's voice session ended unexpectedly." if started else "Miko could not start the voice session. Check the bot logs and Azure setup."
            await interaction.followup.send(message, ephemeral=True)
        finally:
            session.connection = None
            session.participants.clear()
            if bridge is not None:
                bridge.cancel()
                await asyncio.gather(bridge, return_exceptions=True)
            if voice is not None:
                await voice.disconnect(force=True)

    @miko.command(name="listen", description="Start a group voice session with Miko in your channel")
    @app_commands.guild_only()
    async def listen(self, interaction: discord.Interaction):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Only the bot owner can start a voice session.", ephemeral=True)
            return
        config = tuple(os.getenv(key, "").strip() for key in CONFIG_KEYS)
        if not all(config):
            await interaction.response.send_message("Miko voice is missing its Foundry settings.", ephemeral=True)
            return
        state = getattr(interaction.user, "voice", None)
        channel = state.channel if state else None
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
            return
        if interaction.guild_id in self.sessions or interaction.guild.voice_client is not None:
            await interaction.response.send_message("Miko already has a voice connection in this server.", ephemeral=True)
            return
        permissions = channel.permissions_for(interaction.guild.me)
        if not (permissions.view_channel and permissions.connect and permissions.speak and permissions.send_messages):
            await interaction.response.send_message(
                "Miko needs View Channel, Connect, Speak, and Send Messages in this channel for the mic notice.",
                ephemeral=True,
            )
            return

        # Reserve the session before the next await to prevent simultaneous joins.
        session = VoiceSession(interaction.user.id, channel.id)
        self.sessions[interaction.guild_id] = session
        session.task = asyncio.create_task(self.start_after_defer(interaction, channel, config, session))

    async def start_after_defer(self, interaction, channel, config, session):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            if getattr(getattr(interaction.user, "voice", None), "channel", None) != channel:
                await interaction.followup.send("Join the voice channel again to start Miko.", ephemeral=True)
                return
            await self.run_session(interaction, channel, config, session)
        finally:
            if self.sessions.get(interaction.guild_id) is session:
                self.sessions.pop(interaction.guild_id, None)

    @miko.command(name="optin", description="Send your microphone audio to Azure Foundry for this session")
    @app_commands.guild_only()
    async def optin(self, interaction: discord.Interaction):
        session = self.sessions.get(interaction.guild_id)
        state = getattr(interaction.user, "voice", None)
        channel = state.channel if state else None
        if not session or not session.connection or not channel or channel.id != session.channel_id or session.task.cancelling():
            await interaction.response.send_message("Join Miko's active voice channel first.", ephemeral=True)
            return
        session.participants.setdefault(interaction.user.id, object())
        await interaction.response.send_message(
            "Azure Foundry will process your mic audio. If Fish Audio is enabled, it receives Miko's reply text. "
            "Use /miko optout to stop sharing new mic audio.",
            ephemeral=True,
        )

    @miko.command(name="optout", description="Stop sending your microphone audio to Azure Foundry")
    @app_commands.guild_only()
    async def optout(self, interaction: discord.Interaction):
        session = self.sessions.get(interaction.guild_id)
        if session:
            session.participants.pop(interaction.user.id, None)
        await interaction.response.send_message("Your mic is off for Miko.", ephemeral=True)

    @miko.command(name="leave", description="End Miko's voice session")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction):
        session = self.sessions.get(interaction.guild_id)
        if session is None:
            await interaction.response.send_message("Miko has no active voice session here.", ephemeral=True)
            return
        if interaction.user.id != session.owner_id and not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Only the bot owner can end this session.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not session.task.cancelling():
            session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)
        await interaction.followup.send("Miko's voice session ended.", ephemeral=True)

    async def operator(self, interaction):
        if interaction.permissions.administrator or await self.bot.is_owner(interaction.user):
            return True
        await interaction.response.send_message("This command is for server administrators and the bot owner.", ephemeral=True)
        return False

    async def status_embed(self, interaction):
        if not await self.operator(interaction):
            return
        guild_id = interaction.guild_id
        session = self.sessions.get(guild_id)
        output = session.output if session else None
        fish = self.fish
        metrics = fish.metrics
        now = metrics.last_success
        requested = self.providers.get(guild_id, "fish")
        reference = output.voice_id if output else self.voices.get(guild_id, fish.voice_id)
        if not VOICE_ID.fullmatch(reference):
            reference = "invalid reference ID"
        provider = (output.last_provider or output.provider) if output else None
        phase = ("playing" if output.playback.playing_reply() else output.phase) if output else "idle"
        fallback = "available through Foundry" if not output or output.fallback_allowed else "rejected by Foundry"
        fish_health = fish.unavailable(reference) or "available"
        if output and requested == "fish" and output.provider == "azure" and fish_health == "available":
            fish_health = "using Azure for this session"

        def average(total, count):
            return f"{total / count:.2f} s" if count else "—"

        embed = discord.Embed(title="Miko voice status", color=discord.Color.from_rgb(141, 93, 177))
        embed.add_field(name="Provider", value=f"Active: {provider or 'disconnected'}\nNext session: {requested}")
        embed.add_field(name="Fish Audio", value=f"{MODEL}\nVoice: `{reference}`\nHealth: {fish_health}")
        embed.add_field(name="Azure fallback", value=fallback)
        embed.add_field(name="Discord", value=(
            f"<#{session.channel_id}> · {len(session.participants)} opted in" if session else "Disconnected"
        ))
        embed.add_field(name="Playback", value=f"{phase}\nPending: {int(phase == 'queued')}")
        embed.add_field(name="Fish Audio requests", value=(
            f"{metrics.success} completed · {metrics.failures} failed\n"
            f"Last success: {discord.utils.format_dt(now, 'R') if now else '—'}\n"
            f"Last error: {metrics.last_error or '—'}"
        ))
        embed.add_field(name="Average timings", value=(
            f"AI: {average(metrics.total_ai_time, metrics.ai_count)} · "
            f"Queue: {average(metrics.total_queue_time, metrics.queue_count)}\n"
            f"Synthesis: {average(metrics.total_synthesis, metrics.success)} · "
            f"Playback start: {average(metrics.total_playback_start, metrics.playback_count)}\n"
            f"End of speech to reply: {average(metrics.total_response_time, metrics.response_count)}"
        ), inline=False)
        embed.set_footer(text="Fish Audio totals are shared across the bot.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tts.command(name="status", description="See Miko's speech provider, health, and latency")
    @app_commands.guild_only()
    async def tts_status(self, interaction: discord.Interaction):
        await self.status_embed(interaction)

    @voice.command(name="status", description="See Miko's voice connection and playback status")
    @app_commands.guild_only()
    async def voice_status(self, interaction: discord.Interaction):
        await self.status_embed(interaction)

    @tts.command(name="provider", description="Select the speech provider for the next voice session")
    @app_commands.guild_only()
    @app_commands.choices(provider=[
        app_commands.Choice(name="Fish Audio S2.1 Pro Free", value="fish"),
        app_commands.Choice(name="Azure Foundry", value="azure"),
    ])
    async def tts_provider(self, interaction: discord.Interaction, provider: app_commands.Choice[str]):
        if await self.operator(interaction):
            self.providers[interaction.guild_id] = provider.value
            await interaction.response.send_message(f"Next voice session: {provider.name}.", ephemeral=True)

    @tts.command(name="voice", description="Select the Fish Audio reference for the next voice session")
    @app_commands.guild_only()
    async def tts_voice(self, interaction: discord.Interaction, reference_id: str):
        if not await self.operator(interaction):
            return
        reference_id = reference_id.strip().lower()
        if not VOICE_ID.fullmatch(reference_id):
            await interaction.response.send_message("Enter the 32-character hexadecimal Fish Audio voice ID.", ephemeral=True)
            return
        self.voices[interaction.guild_id] = reference_id
        await interaction.response.send_message("Saved the reference for the next voice session.", ephemeral=True)

    @tts.command(name="reset", description="Reset this server's voice overrides and check provider recovery")
    @app_commands.guild_only()
    async def tts_reset(self, interaction: discord.Interaction):
        if not await self.operator(interaction):
            return
        self.providers.pop(interaction.guild_id, None)
        self.voices.pop(interaction.guild_id, None)
        if await self.bot.is_owner(interaction.user):
            self.fish.reset()
        await interaction.response.send_message("The next voice session will use the configured defaults.", ephemeral=True)

    @tts.command(name="test", description="Play a short Fish Audio test with a speech cue")
    @app_commands.guild_only()
    @app_commands.choices(style=[
        app_commands.Choice(name=cue.title(), value=cue)
        for cue in ("neutral", *DIRECTIONS)
    ])
    async def tts_test(self, interaction: discord.Interaction, style: app_commands.Choice[str], text: str):
        if not await self.operator(interaction):
            return
        session = self.sessions.get(interaction.guild_id)
        state = getattr(interaction.user, "voice", None)
        channel = state.channel if state else None
        if not session or not session.connection or getattr(channel, "id", None) != session.channel_id:
            await interaction.response.send_message("Join Miko's active voice channel to test speech.", ephemeral=True)
            return
        output = session.output
        if output.provider != "fish" or self.fish.unavailable(output.voice_id):
            await interaction.response.send_message("Fish Audio is unavailable in this session. Check /tts status.", ephemeral=True)
            return
        if output.phase != "idle" or output.playback.playing_reply():
            await interaction.response.send_message("Wait for Miko's current reply to finish.", ephemeral=True)
            return
        if len(text) > 300:
            await interaction.response.send_message("Use at most 300 characters for a test.", ephemeral=True)
            return
        cue = f"[{style.value}] " if style.value != "neutral" else ""
        reply = parse_reply(json.dumps({"display_text": text, "speech_text": cue + text}))
        if not reply.should_speak:
            await interaction.response.send_message("Enter some text for Miko to say.", ephemeral=True)
            return
        output.play(session.connection, reply, asyncio.get_running_loop().time())
        await interaction.response.send_message("Speech test queued.", ephemeral=True)

    @voice.command(name="stop", description="Discard Miko's current and pending speech")
    @app_commands.guild_only()
    async def voice_stop(self, interaction: discord.Interaction):
        if not await self.operator(interaction):
            return
        session = self.sessions.get(interaction.guild_id)
        if not session or not session.connection:
            await interaction.response.send_message("Miko has no active voice session here.", ephemeral=True)
            return
        session.output.suppress_until_input = True
        session.output.skip()
        await interaction.response.send_message("Miko's current speech was cleared.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        session = self.sessions.get(member.guild.id)
        if not session or before.channel == after.channel or getattr(before.channel, "id", None) != session.channel_id:
            return
        session.participants.pop(member.id, None)
        if member.id in (session.owner_id, self.bot.user.id) and not session.task.cancelling():
            session.task.cancel()


async def setup(bot):
    await bot.add_cog(MikoVoice(bot))
