"""Opt-in Discord voice commands for Miko."""
import asyncio
import logging
import os
from dataclasses import dataclass, field

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


class MikoVoice(commands.Cog):
    miko = app_commands.Group(name="miko", description="Control Miko's live voice")

    def __init__(self, bot):
        self.bot = bot
        self.sessions = {}

    async def cog_unload(self):
        tasks = [session.task for session in self.sessions.values() if session.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def run_session(self, interaction, channel, config, session):
        ready = asyncio.Event()
        voice = None
        bridge = None
        started = False
        try:
            from discord.ext.voice_recv import VoiceRecvClient
            from services.foundry_voice import run_voice

            voice = await channel.connect(cls=VoiceRecvClient, timeout=15, reconnect=False, self_deaf=False)
            bridge = asyncio.create_task(run_voice(voice, session.participants, config, ready))
            wait_ready = asyncio.create_task(ready.wait())
            try:
                done, _ = await asyncio.wait(
                    (bridge, wait_ready), timeout=20, return_when=asyncio.FIRST_COMPLETED,
                )
                if bridge in done:
                    bridge.result()
                if not ready.is_set():
                    raise RuntimeError("The Foundry voice stream did not start.")
            finally:
                wait_ready.cancel()
                await asyncio.gather(wait_ready, return_exceptions=True)

            await interaction.followup.send(
                "Miko is ready. Anyone in this channel can use /miko optin to share their mic with Azure "
                "or /miko optout to stop. Take turns speaking. Use /miko leave to end the session.",
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
        if not (permissions.view_channel and permissions.connect and permissions.speak):
            await interaction.response.send_message("Miko needs View Channel, Connect, and Speak in this channel.", ephemeral=True)
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
        if not session or not channel or channel.id != session.channel_id or session.task.cancelling():
            await interaction.response.send_message("Join Miko's active voice channel first.", ephemeral=True)
            return
        session.participants.setdefault(interaction.user.id, object())
        await interaction.response.send_message(
            "Your mic is enabled for this session. Use /miko optout to stop sending audio to Azure.",
            ephemeral=True,
        )

    @miko.command(name="optout", description="Stop sending your microphone audio to Azure Foundry")
    @app_commands.guild_only()
    async def optout(self, interaction: discord.Interaction):
        session = self.sessions.get(interaction.guild_id)
        if session:
            session.participants.pop(interaction.user.id, None)
        await interaction.response.send_message("Your mic is off for Miko.", ephemeral=True)

    @miko.command(name="leave", description="End Miko's Azure voice session")
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
