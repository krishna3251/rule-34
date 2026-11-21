from __future__ import annotations

import asyncio
import io
import json
import logging
from collections import OrderedDict
from contextlib import suppress
from functools import partial
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import discord
from discord.ext import commands
from discord import app_commands

from services.miko_chat_engine import MikoChatEngine
from services.access_policy import content_access, is_age_restricted

logger = logging.getLogger("discord_bot")


class MikoChat(commands.Cog):
    """Thin Discord adapter for the single Miko Chat Engine."""

    WAIT_SECONDS = 2.0

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_file = Path("data/miko_chat.json")
        self.data_file.parent.mkdir(parents=True, exist_ok=True)

        self.chat_channels: dict[int, int] = {}
        self.disabled_channels: set[int] = set()
        self.quiet_channels: set[int] = set()
        self.channel_levels: dict[int, int] = {}
        self.recent_images: OrderedDict[tuple[int, int, int], tuple[int, float]] = OrderedDict()
        self.chat_engine = MikoChatEngine()
        self.chat_engine.bind_chat_cog(self)

        self.load_data()

    async def cog_unload(self):
        await self.chat_engine.close()

    def _channel_level(self, channel) -> int:
        parent = getattr(channel, "parent", None)
        return self.channel_levels.get(channel.id, self.channel_levels.get(getattr(parent, "id", None), 1))

    async def _flirt_on(self, guild, channel, user) -> str:
        if not await content_access(self.bot, guild, channel, user):
            return "Use an age-restricted server channel and complete this server's /verify first."
        if self._channel_level(channel) == 0:
            return "Flirting is disabled here by an administrator. Ask them to use /mikosetlevel with level 1 or 2."
        await asyncio.to_thread(self.chat_engine.profile.enable_flirting, user.id, guild.id, channel.id)
        return "Light, non-explicit flirting is on for you in this channel. Use /flirt off or say ‘stop flirting’ anytime."

    async def _flirt_off(self, user) -> str:
        await asyncio.to_thread(self.chat_engine.profile.disable_flirting, user.id)
        return "Flirting is off for you everywhere. Your other channel opt-ins have been cleared."

    flirt = app_commands.Group(name="flirt", description="Choose your personal Miko chat tone")

    @flirt.command(name="on", description="Opt into light flirting in this verified, age-restricted channel")
    async def flirt_on_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        text = await self._flirt_on(interaction.guild, interaction.channel, interaction.user)
        await interaction.followup.send(text, ephemeral=True)

    @flirt.command(name="off", description="Stop flirting everywhere; available in any channel or DM")
    async def flirt_off_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await self._flirt_off(interaction.user), ephemeral=True)

    @flirt.command(name="status", description="Check your personal setting and this channel's access")
    async def flirt_status_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        profile = self.chat_engine.profile.get(interaction.user.id, interaction.guild_id, interaction.channel_id)
        allowed = await content_access(self.bot, interaction.guild, interaction.channel, interaction.user)
        level = self._channel_level(interaction.channel) if interaction.channel else 0
        opted = profile.get("flirt_opt_in") is True and self.chat_engine._profile_level(profile) > 0
        await interaction.followup.send(
            f"Your opt-in here: {'on' if opted else 'off'}\n"
            f"Verified age-restricted channel: {'yes' if allowed else 'no'}\n"
            f"Administrator chat level: {level}\n"
            f"Flirting active: {'yes' if opted and allowed and level > 0 else 'no'}\n"
            f"AI provider: {'configured' if self.chat_engine.ai_ready else 'not configured'}", ephemeral=True)

    @commands.command(name="flirt")
    async def flirt_prefix(self, ctx: commands.Context, mode: str = "") -> None:
        mode = mode.strip().casefold()
        if mode == "off":
            text = await self._flirt_off(ctx.author)
        elif mode == "on":
            text = await self._flirt_on(ctx.guild, ctx.channel, ctx.author)
        else:
            text = "Use `flirt on` to opt in here or `flirt off` to stop everywhere."
        await ctx.send(text, allowed_mentions=discord.AllowedMentions.none())

    def load_data(self) -> None:
        try:
            if not self.data_file.exists():
                return

            raw = json.loads(self.data_file.read_text(encoding="utf-8"))
            self.chat_channels = {
                int(k): int(v)
                for k, v in raw.get("chat_channels", {}).items()
            }
            self.disabled_channels = {
                int(v) for v in raw.get("disabled_channels", [])
            }
            self.quiet_channels = {
                int(v) for v in raw.get("quiet_channels", [])
            }
            self.channel_levels = {
                int(k): int(v)
                for k, v in raw.get("channel_levels", {}).items()
            }
        except (OSError, ValueError, TypeError) as exc:
            logger.error("Could not load Miko configuration: %s", exc)
            self.chat_channels = {}
            self.disabled_channels = set()
            self.quiet_channels = set()
            self.channel_levels = {}

    def save_data(self) -> None:
        try:
            payload = {
                "chat_channels": {
                    str(k): v for k, v in self.chat_channels.items()
                },
                "disabled_channels": sorted(self.disabled_channels),
                "quiet_channels": sorted(self.quiet_channels),
                "channel_levels": {
                    str(k): max(0, min(2, v))
                    for k, v in self.channel_levels.items()
                },
            }
            self.data_file.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Could not save Miko configuration: %s", exc)

    async def _send_chat(self, send, result, scope):
        emotion_id = result.emotion_id
        last = self.recent_images.get(scope)
        repeated = last and last[0] == emotion_id and monotonic() - last[1] < 2700
        if result.attach_emotion and not repeated:
            picture = await asyncio.to_thread(self.chat_engine.emotion.public_image, emotion_id)
            if picture:
                with io.BytesIO(picture) as buffer:
                    file = discord.File(buffer, filename=f"miko_{emotion_id:02d}.webp")
                    try:
                        sent = await send(result.text, file=file, allowed_mentions=discord.AllowedMentions.none())
                    except discord.HTTPException as exc:
                        if exc.status not in {403, 413} and exc.code != 40005:
                            raise
                        logger.warning("Discord rejected Miko sticker %02d (HTTP %s, code %s)",
                                       emotion_id, exc.status, exc.code)
                    else:
                        self.recent_images[scope] = (emotion_id, monotonic())
                        self.recent_images.move_to_end(scope)
                        if len(self.recent_images) > 256:
                            self.recent_images.popitem(last=False)
                        return sent
                    finally:
                        file.close()
        return await send(result.text, allowed_mentions=discord.AllowedMentions.none())

    @staticmethod
    async def _try_reaction(action, *args):
        try:
            async with asyncio.timeout(2.0):
                await action(*args)
        except (discord.HTTPException, TimeoutError):
            pass

    async def _deliver_chat(self, request, send, message, *, source=None, direct=False):
        waiting_task = None
        attempted = False
        started = None
        result = sent = None

        async def wait_reaction():
            nonlocal attempted
            await asyncio.sleep(self.WAIT_SECONDS)
            attempted = True
            await self._try_reaction(source.add_reaction, "⏳")

        def start_wait():
            nonlocal started, waiting_task
            started = monotonic()
            if source is not None:
                waiting_task = asyncio.create_task(wait_reaction())

        try:
            result = await request(on_wait=start_wait)
            waited = started is not None and monotonic() - started >= self.WAIT_SECONDS
            if result.replied and result.text:
                scope = (getattr(message.guild, "id", 0) or 0, message.channel.id, message.author.id)
                sent = await self._send_chat(send, result, scope)
            elif direct or (started is not None and result.reason == "timeout"):
                status = {
                    "channel_disabled": "Miko chat is disabled in this channel.",
                    "timeout": "I took too long to answer. Try again in a moment.",
                    "busy": "I’m still working on another request. Try again in a moment.",
                    "user_cooldown": "Give me a moment before asking again.",
                    "channel_cooldown": "Give me a moment before asking again.",
                    "daily_quota": "Miko’s daily chat limit has been reached.",
                }
                await send(status.get(result.reason, "I couldn’t answer that. Try again in a moment."),
                           allowed_mentions=discord.AllowedMentions.none())
        finally:
            if waiting_task is not None:
                if not attempted:
                    waiting_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await waiting_task
                else:
                    try:
                        await waiting_task
                    finally:
                        await self._try_reaction(source.remove_reaction, "⏳", self.bot.user)

        if waited and sent is not None and result.completed:
            target = source if source is not None else sent
            await self._try_reaction(target.add_reaction, "✅")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        channel_id = message.channel.id
        if message.author.bot or channel_id in self.disabled_channels:
            return
        auto_chat = bool(message.guild) and self.chat_channels.get(message.guild.id) == channel_id
        quiet = channel_id in self.quiet_channels
        if not await self.chat_engine.is_candidate(message, self.bot, auto_chat=auto_chat, quiet=quiet):
            return

        request = partial(self.chat_engine.handle, message, self.bot, auto_chat=auto_chat,
                          admin_level=self._channel_level(message.channel),
                          disabled=channel_id in self.disabled_channels, quiet=quiet)
        async with message.channel.typing():
            await self._deliver_chat(request, partial(message.reply, mention_author=False),
                                     message, source=message)

    @commands.command(name="mikosetchat", aliases=["setchat"])
    @commands.has_permissions(administrator=True)
    async def miko_set_chat(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | None = None,
    ) -> None:
        channel = channel or ctx.channel
        self.chat_channels[ctx.guild.id] = channel.id
        self.save_data()

        status = (
            "AI connected"
            if self.chat_engine.ai_ready
            else "AI not configured"
        )
        await ctx.send(f"Miko auto-chat set to {channel.mention}. {status}")

    @commands.command(name="mikounsetchat", aliases=["unsetchat"])
    @commands.has_permissions(administrator=True)
    async def miko_unset_chat(self, ctx: commands.Context) -> None:
        removed = self.chat_channels.pop(ctx.guild.id, None)
        self.save_data()
        await ctx.send(
            "Miko auto-chat removed."
            if removed
            else "No Miko auto-chat channel was configured."
        )

    @commands.command(name="mikosetlevel", aliases=["setlevel"])
    @commands.has_permissions(administrator=True)
    async def miko_set_level(
        self,
        ctx: commands.Context,
        level: int,
        channel: discord.TextChannel | None = None,
    ) -> None:
        channel = channel or ctx.channel
        level = max(0, min(2, level))

        if level == 2 and not is_age_restricted(channel):
            await ctx.send(
                "Level 2 is only available in an age-restricted channel."
            )
            return

        self.channel_levels[channel.id] = level
        self.save_data()
        await ctx.send(
            f"Miko level for {channel.mention} is now {level}."
        )

    @commands.command(name="mikodisabled", aliases=["disable"])
    @commands.has_permissions(administrator=True)
    async def miko_disabled(self, ctx: commands.Context) -> None:
        self.disabled_channels.add(ctx.channel.id)
        self.save_data()
        await ctx.send("Miko is disabled in this channel.")

    @commands.command(name="mikoenabled", aliases=["enable"])
    @commands.has_permissions(administrator=True)
    async def miko_enabled(self, ctx: commands.Context) -> None:
        self.disabled_channels.discard(ctx.channel.id)
        self.save_data()
        await ctx.send("Miko is enabled in this channel.")

    @commands.command(name="mikoquiet", aliases=["quiet"])
    @commands.has_permissions(administrator=True)
    async def miko_quiet(self, ctx: commands.Context) -> None:
        if ctx.channel.id in self.quiet_channels:
            self.quiet_channels.remove(ctx.channel.id)
            status = "disabled"
        else:
            self.quiet_channels.add(ctx.channel.id)
            status = "enabled"

        self.save_data()
        await ctx.send(f"Miko quiet mode {status} in this channel.")

    @commands.command(name="mikochatinfo", aliases=["chatinfo"])
    async def miko_chat_info(self, ctx: commands.Context) -> None:
        channel_id = self.chat_channels.get(ctx.guild.id) if ctx.guild else None
        channel = ctx.guild.get_channel(channel_id) if channel_id else None

        level = self._channel_level(ctx.channel)
        disabled = "yes" if ctx.channel.id in self.disabled_channels else "no"
        quiet = "yes" if ctx.channel.id in self.quiet_channels else "no"

        await ctx.send(
            "Miko Chat\n"
            f"Auto-chat: {channel.mention if channel else 'Not configured'}\n"
            f"AI: {'configured' if self.chat_engine.ai_ready else 'not configured'}\n"
            f"Primary: {self.chat_engine.provider} / {self.chat_engine.model}\n"
            f"Live web: {'enabled' if self.chat_engine.web_search_ready else 'unavailable'}\n"
            f"Agent tools: {len(self.chat_engine.tool_names)}\n"
            "Memory: private user/channel context / 45 min expiry; /mikobrain controls\n"
            f"Channel level: {level}\n"
            f"Disabled: {disabled}\n"
            f"Quiet mode: {quiet}"
        )

    @commands.command(name="resetmiko", aliases=["resetmemory", "resetconvo"])
    async def reset_miko(self, ctx: commands.Context) -> None:
        await self.chat_engine.reset_context(
            ctx.author.id,
            getattr(ctx.guild, "id", None),
            ctx.channel.id,
        )
        await ctx.send("Miko memory cleared for you.")

    @commands.command(name="forgetmiko", aliases=["forgetme"])
    async def forget_miko(self, ctx: commands.Context) -> None:
        await self.chat_engine.clear_user(ctx.author.id)
        await ctx.send(
            "Your stored Miko memory and profile have been deleted."
        )

    @commands.command(name="stopflirting")
    async def stop_flirting(self, ctx: commands.Context) -> None:
        await ctx.send(await self._flirt_off(ctx.author))

    @commands.command(name="allowflirting")
    async def allow_flirting(self, ctx: commands.Context) -> None:
        await ctx.send(await self._flirt_on(ctx.guild, ctx.channel, ctx.author))

    @commands.command(name="askmiko", aliases=["mikoask"])
    async def ask_miko(
        self,
        ctx: commands.Context,
        *,
        question: str,
    ) -> None:
        message = SimpleNamespace(id=ctx.message.id, author=ctx.author, channel=ctx.channel,
                                  guild=ctx.guild, content=question.strip())
        request = partial(self.chat_engine.ask_direct, message, self.bot, prompt=message.content,
                          admin_level=self._channel_level(ctx.channel),
                          disabled=ctx.channel.id in self.disabled_channels)
        async with ctx.typing():
            await self._deliver_chat(request, ctx.send, message, source=ctx.message, direct=True)

    @app_commands.command(name="mikosetchat", description="Set Miko auto-chat in a channel")
    @app_commands.describe(channel="Channel where Miko should automatically chat")
    @app_commands.checks.has_permissions(administrator=True)
    async def miko_set_chat_slash(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("❌ Please use this in a text channel.", ephemeral=True)
            return
        self.chat_channels[interaction.guild_id] = target.id
        self.save_data()
        status = "AI connected" if self.chat_engine.ai_ready else "AI not configured"
        await interaction.response.send_message(
            f"Miko auto-chat set to {target.mention}. {status}"
        )

    @app_commands.command(name="mikounsetchat", description="Remove Miko auto-chat")
    @app_commands.checks.has_permissions(administrator=True)
    async def miko_unset_chat_slash(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("❌ Server only.", ephemeral=True)
            return
        removed = self.chat_channels.pop(interaction.guild_id, None)
        self.save_data()
        await interaction.response.send_message(
            "Miko auto-chat removed."
            if removed
            else "No Miko auto-chat channel was configured."
        )

    @app_commands.command(name="mikosetlevel", description="Set Miko chat level 0-2")
    @app_commands.describe(level="0=clean, 1=playful, 2=stronger mature teasing", channel="Channel to configure")
    @app_commands.checks.has_permissions(administrator=True)
    async def miko_set_level_slash(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 0, 2],
        channel: discord.TextChannel | None = None,
    ) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("❌ Please use this in a text channel.", ephemeral=True)
            return
        if int(level) == 2 and not is_age_restricted(target):
            await interaction.response.send_message("Level 2 is only available in an age-restricted channel.", ephemeral=True)
            return
        self.channel_levels[target.id] = int(level)
        self.save_data()
        await interaction.response.send_message(
            f"Miko level for {target.mention} is now {int(level)}."
        )

    @app_commands.command(name="mikodisabled", description="Disable Miko in this channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def miko_disabled_slash(self, interaction: discord.Interaction) -> None:
        self.disabled_channels.add(interaction.channel_id)
        self.save_data()
        await interaction.response.send_message("Miko is disabled in this channel.")

    @app_commands.command(name="mikoenabled", description="Enable Miko in this channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def miko_enabled_slash(self, interaction: discord.Interaction) -> None:
        self.disabled_channels.discard(interaction.channel_id)
        self.save_data()
        await interaction.response.send_message("Miko is enabled in this channel.")

    @app_commands.command(name="mikoquiet", description="Toggle Miko quiet mode in this channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def miko_quiet_slash(self, interaction: discord.Interaction) -> None:
        if interaction.channel_id in self.quiet_channels:
            self.quiet_channels.remove(interaction.channel_id)
            status = "disabled"
        else:
            self.quiet_channels.add(interaction.channel_id)
            status = "enabled"
        self.save_data()
        await interaction.response.send_message(f"Miko quiet mode {status} in this channel.")

    @app_commands.command(name="mikochatinfo", description="Show Miko chat configuration")
    async def miko_chat_info_slash(self, interaction: discord.Interaction) -> None:
        channel_id = self.chat_channels.get(interaction.guild_id or 0)
        channel = interaction.guild.get_channel(channel_id) if interaction.guild and channel_id else None
        level = self._channel_level(interaction.channel)
        disabled = "yes" if interaction.channel_id in self.disabled_channels else "no"
        quiet = "yes" if interaction.channel_id in self.quiet_channels else "no"
        await interaction.response.send_message(
            "Miko Chat\n"
            f"Auto-chat: {channel.mention if channel else 'Not configured'}\n"
            f"AI: {'configured' if self.chat_engine.ai_ready else 'not configured'}\n"
            f"Primary: {self.chat_engine.provider} / {self.chat_engine.model}\n"
            f"Live web: {'enabled' if self.chat_engine.web_search_ready else 'unavailable'}\n"
            f"Agent tools: {len(self.chat_engine.tool_names)}\n"
            "Memory: private user/channel context / 45 min expiry; /mikobrain controls\n"
            f"Channel level: {level}\n"
            f"Disabled: {disabled}\n"
            f"Quiet mode: {quiet}"
        )

    @app_commands.command(name="resetmiko", description="Clear your Miko conversation memory")
    async def reset_miko_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.chat_engine.reset_context(
            interaction.user.id,
            interaction.guild_id,
            interaction.channel_id,
        )
        await interaction.followup.send("Miko memory cleared for you.", ephemeral=True)

    @app_commands.command(name="forgetmiko", description="Delete your stored Miko memory and profile")
    async def forget_miko_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.chat_engine.clear_user(interaction.user.id)
        await interaction.followup.send(
            "Your stored Miko memory and profile have been deleted.",
            ephemeral=True,
        )

    @app_commands.command(name="stopflirting", description="Keep Miko clean for your conversations")
    async def stop_flirting_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await self._flirt_off(interaction.user), ephemeral=True)

    @app_commands.command(name="allowflirting", description="Opt into light flirting in this verified, age-restricted channel")
    async def allow_flirting_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await self._flirt_on(interaction.guild, interaction.channel, interaction.user), ephemeral=True)

    @app_commands.command(name="askmiko", description="Ask Miko a direct question")
    @app_commands.describe(question="What you want to ask Miko")
    async def ask_miko_slash(
        self,
        interaction: discord.Interaction,
        question: str,
    ) -> None:
        question = question.strip()
        if not question:
            await interaction.response.send_message(
                "❌ Please provide a question.",
                ephemeral=True,
            )
            return

        message = SimpleNamespace(id=interaction.id, author=interaction.user,
                                  channel=interaction.channel, guild=interaction.guild, content=question)
        await interaction.response.defer(thinking=True)
        request = partial(self.chat_engine.ask_direct, message, self.bot, prompt=question,
                          admin_level=self._channel_level(interaction.channel),
                          disabled=interaction.channel.id in self.disabled_channels)
        try:
            await self._deliver_chat(request, partial(interaction.followup.send, wait=True), message, direct=True)
        except asyncio.CancelledError:
            with suppress(discord.HTTPException, TimeoutError):
                async with asyncio.timeout(2.0):
                    await interaction.delete_original_response()
            raise


async def setup(bot: commands.Bot):
    await bot.add_cog(MikoChat(bot))
