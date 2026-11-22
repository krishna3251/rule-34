"""Opt-in Discord voice commands for the configured Miko test channel."""
import asyncio
import logging
import os

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


class MikoVoice(commands.Cog):
    miko = app_commands.Group(name="miko", description="Control Miko's live voice")

    def __init__(self, bot):
        self.bot = bot
        self.sessions = {}

    async def cog_unload(self):
        tasks = [task for _, task in self.sessions.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def run_session(self, interaction, channel, config):
        ready = asyncio.Event()
        voice = None
        bridge = None
        started = False
        try:
            from discord.ext.voice_recv import VoiceRecvClient
            from services.foundry_voice import run_voice

            voice = await channel.connect(cls=VoiceRecvClient, timeout=15, reconnect=False, self_deaf=False)
            bridge = asyncio.create_task(run_voice(voice, interaction.user.id, config, ready))
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
                "Miko is sending your mic audio to Azure. Use /miko leave to end the session.", ephemeral=True,
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
                "Miko voice failed (%s, status=%s, foundry_type=%s, foundry_code=%s, session_ready=%s).",
                type(exc).__name__,
                getattr(exc, "status_code", None),
                getattr(exc, "error_type", None),
                getattr(exc, "error_code", None),
                ready.is_set(),
            )
            message = "Miko's voice session ended unexpectedly." if started else "Miko could not start the voice session. Check the bot logs and Azure setup."
            await interaction.followup.send(message, ephemeral=True)
        finally:
            self.sessions.pop(interaction.guild_id, None)
            if bridge is not None:
                bridge.cancel()
                await asyncio.gather(bridge, return_exceptions=True)
            if voice is not None:
                await voice.disconnect(force=True)

    @miko.command(name="listen", description="Start an Azure voice session in Miko's test channel")
    @app_commands.guild_only()
    async def listen(self, interaction: discord.Interaction):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Only the bot owner can start a voice test.", ephemeral=True)
            return
        config = tuple(os.getenv(key, "").strip() for key in CONFIG_KEYS)
        channel_id = os.getenv("MIKO_VOICE_CHANNEL_ID", "").strip()
        if not all(config) or not channel_id.isdecimal():
            await interaction.response.send_message("Miko voice is missing its Foundry settings or test channel ID.", ephemeral=True)
            return
        state = getattr(interaction.user, "voice", None)
        channel = state.channel if state else None
        if not isinstance(channel, discord.VoiceChannel) or channel.id != int(channel_id):
            await interaction.response.send_message("Join the configured Miko Test voice channel first.", ephemeral=True)
            return
        if interaction.guild_id in self.sessions or interaction.guild.voice_client is not None:
            await interaction.response.send_message("Miko already has a voice connection in this server.", ephemeral=True)
            return
        permissions = channel.permissions_for(interaction.guild.me)
        if not (permissions.connect and permissions.speak):
            await interaction.response.send_message("Miko needs Connect and Speak in the test channel.", ephemeral=True)
            return

        # Reserve the session before the next await to prevent simultaneous joins.
        task = asyncio.create_task(self.start_after_defer(interaction, channel, config))
        self.sessions[interaction.guild_id] = (interaction.user.id, task)

    async def start_after_defer(self, interaction, channel, config):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self.run_session(interaction, channel, config)
        finally:
            session = self.sessions.get(interaction.guild_id)
            if session and session[1] is asyncio.current_task():
                self.sessions.pop(interaction.guild_id, None)

    @miko.command(name="leave", description="End Miko's Azure voice session")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction):
        session = self.sessions.get(interaction.guild_id)
        if session is None:
            await interaction.response.send_message("Miko has no active voice test here.", ephemeral=True)
            return
        owner_id, task = session
        if interaction.user.id != owner_id and not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Only the bot owner can end this session.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await interaction.followup.send("Miko's voice session ended.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        session = self.sessions.get(member.guild.id)
        if session and before.channel != after.channel and member.id in (session[0], self.bot.user.id):
            if before.channel is not None and not session[1].cancelling():
                session[1].cancel()


async def setup(bot):
    await bot.add_cog(MikoVoice(bot))
