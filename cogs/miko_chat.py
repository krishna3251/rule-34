from __future__ import annotations

import json
import logging
from pathlib import Path

import discord
from discord.ext import commands
from discord import app_commands

from services.miko_chat_engine import MikoChatEngine

logger = logging.getLogger("discord_bot")


class MikoChat(commands.Cog):
    """Thin Discord adapter for the single Miko Chat Engine."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_file = Path("data/miko_chat.json")
        self.data_file.parent.mkdir(parents=True, exist_ok=True)

        self.chat_channels: dict[int, int] = {}
        self.disabled_channels: set[int] = set()
        self.quiet_channels: set[int] = set()
        self.channel_levels: dict[int, int] = {}
        self.chat_engine = MikoChatEngine()
        self.chat_engine.bind_chat_cog(self)

        self.load_data()

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

    def _emotion_file(self, emotion_id: int) -> Path | None:
        path = self.chat_engine.emotion.image_path(emotion_id)
        return path if path.is_file() else None

    async def _reply_with_emotion(
        self,
        message: discord.Message,
        text: str,
        emotion_id: int,
    ) -> None:
        image_path = self._emotion_file(emotion_id)
        if image_path is None:
            await message.reply(text, mention_author=False)
            return

        file = discord.File(
            image_path,
            filename=f"miko_{emotion_id:02d}{image_path.suffix}",
        )
        await message.reply(
            text,
            file=file,
            mention_author=False,
        )

    async def _handle_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        auto_chat = (
            bool(message.guild)
            and self.chat_channels.get(message.guild.id) == message.channel.id
        )
        channel_id = message.channel.id

        result = await self.chat_engine.handle(
            message,
            self.bot,
            auto_chat=auto_chat,
            admin_level=self.channel_levels.get(channel_id, 1),
            disabled=channel_id in self.disabled_channels,
            quiet=channel_id in self.quiet_channels,
        )

        if result.replied and result.text:
            await self._reply_with_emotion(
                message,
                result.text,
                result.emotion_id,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        auto_chat = (
            bool(message.guild)
            and self.chat_channels.get(message.guild.id) == message.channel.id
        )
        channel_id = message.channel.id
        disabled = channel_id in self.disabled_channels
        quiet = channel_id in self.quiet_channels

        candidate = await self.chat_engine.is_candidate(
            message,
            self.bot,
            auto_chat=auto_chat,
            quiet=quiet,
        )
        if disabled or not candidate:
            return

        async with message.channel.typing():
            result = await self.chat_engine.handle(
                message,
                self.bot,
                auto_chat=auto_chat,
                admin_level=self.channel_levels.get(channel_id, 1),
                disabled=disabled,
                quiet=quiet,
            )

        if result.replied and result.text:
            await self._reply_with_emotion(
                message,
                result.text,
                result.emotion_id,
            )

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

        if level == 2 and not channel.is_nsfw():
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
        channel_id = self.chat_channels.get(ctx.guild.id)
        channel = ctx.guild.get_channel(channel_id) if channel_id else None

        level = self.channel_levels.get(ctx.channel.id, 1)
        disabled = "yes" if ctx.channel.id in self.disabled_channels else "no"
        quiet = "yes" if ctx.channel.id in self.quiet_channels else "no"

        await ctx.send(
            "Miko Chat\n"
            f"Auto-chat: {channel.mention if channel else 'Not configured'}\n"
            f"AI: {'connected' if self.chat_engine.ai_ready else 'not configured'}\n"
            f"Primary: {self.chat_engine.provider} / {self.chat_engine.model}\n"
            f"Live web: {'enabled' if self.chat_engine.web_search_ready else 'unavailable'}\n"
            f"Agent tools: {len(self.chat_engine.tool_names)}\n"
            "Memory: scoped user + channel context / 45 min expiry\n"
            f"Channel level: {level}\n"
            f"Disabled: {disabled}\n"
            f"Quiet mode: {quiet}"
        )

    @commands.command(name="resetmiko", aliases=["resetmemory", "resetconvo"])
    async def reset_miko(self, ctx: commands.Context) -> None:
        self.chat_engine.reset_memory(
            ctx.author.id,
            getattr(ctx.guild, "id", None),
            ctx.channel.id,
        )
        await ctx.send("Miko memory cleared for you.")

    @commands.command(name="forgetmiko", aliases=["forgetme"])
    async def forget_miko(self, ctx: commands.Context) -> None:
        self.chat_engine.forget_user(ctx.author.id)
        await ctx.send(
            "Your stored Miko memory and profile have been deleted."
        )

    @commands.command(name="stopflirting")
    async def stop_flirting(self, ctx: commands.Context) -> None:
        self.chat_engine.profile.set(ctx.author.id, "level_cap", 0)
        await ctx.send("Understood. Miko will keep things clean for you.")

    @commands.command(name="allowflirting")
    async def allow_flirting(self, ctx: commands.Context) -> None:
        self.chat_engine.profile.set(ctx.author.id, "level_cap", 2)
        await ctx.send("Your personal Miko level cap has been restored.")

    @commands.command(name="askmiko", aliases=["mikoask"])
    async def ask_miko(
        self,
        ctx: commands.Context,
        *,
        question: str,
    ) -> None:
        class DirectMessage:
            pass

        message = DirectMessage()
        message.author = ctx.author
        message.channel = ctx.channel
        message.guild = ctx.guild
        message.content = question.strip()

        async with ctx.typing():
            result = await self.chat_engine.ask_direct(
                message,
                self.bot,
                prompt=question,
                admin_level=self.channel_levels.get(ctx.channel.id, 1),
            )

        if not result.replied or not result.text:
            await ctx.send(
                f"Miko did not respond ({result.reason or 'no_response'})."
            )
            return

        image_path = self._emotion_file(result.emotion_id)
        if image_path is None:
            await ctx.send(result.text)
            return

        await ctx.send(
            result.text,
            file=discord.File(
                image_path,
                filename=f"miko_{result.emotion_id:02d}{image_path.suffix}",
            ),
        )

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
        if int(level) == 2 and not target.is_nsfw():
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
        level = self.channel_levels.get(interaction.channel_id, 1)
        disabled = "yes" if interaction.channel_id in self.disabled_channels else "no"
        quiet = "yes" if interaction.channel_id in self.quiet_channels else "no"
        await interaction.response.send_message(
            "Miko Chat\n"
            f"Auto-chat: {channel.mention if channel else 'Not configured'}\n"
            f"AI: {'connected' if self.chat_engine.ai_ready else 'not configured'}\n"
            f"Primary: {self.chat_engine.provider} / {self.chat_engine.model}\n"
            f"Live web: {'enabled' if self.chat_engine.web_search_ready else 'unavailable'}\n"
            f"Agent tools: {len(self.chat_engine.tool_names)}\n"
            "Memory: scoped user + channel context / 45 min expiry\n"
            f"Channel level: {level}\n"
            f"Disabled: {disabled}\n"
            f"Quiet mode: {quiet}"
        )

    @app_commands.command(name="resetmiko", description="Clear your Miko conversation memory")
    async def reset_miko_slash(self, interaction: discord.Interaction) -> None:
        self.chat_engine.reset_memory(
            interaction.user.id,
            interaction.guild_id,
            interaction.channel_id,
        )
        await interaction.response.send_message("Miko memory cleared for you.", ephemeral=True)

    @app_commands.command(name="forgetmiko", description="Delete your stored Miko memory and profile")
    async def forget_miko_slash(self, interaction: discord.Interaction) -> None:
        self.chat_engine.forget_user(interaction.user.id)
        await interaction.response.send_message(
            "Your stored Miko memory and profile have been deleted.",
            ephemeral=True,
        )

    @app_commands.command(name="stopflirting", description="Keep Miko clean for your conversations")
    async def stop_flirting_slash(self, interaction: discord.Interaction) -> None:
        self.chat_engine.profile.set(interaction.user.id, "level_cap", 0)
        await interaction.response.send_message("Understood. Miko will keep things clean for you.")

    @app_commands.command(name="allowflirting", description="Restore your personal Miko level cap")
    async def allow_flirting_slash(self, interaction: discord.Interaction) -> None:
        self.chat_engine.profile.set(interaction.user.id, "level_cap", 2)
        await interaction.response.send_message("Your personal Miko level cap has been restored.")

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

        class DirectMessage:
            pass

        message = DirectMessage()
        message.author = interaction.user
        message.channel = interaction.channel
        message.guild = interaction.guild
        message.content = question

        await interaction.response.defer()
        result = await self.chat_engine.ask_direct(
            message,
            self.bot,
            prompt=question,
            admin_level=self.channel_levels.get(interaction.channel_id, 1),
        )

        if not result.replied or not result.text:
            await interaction.followup.send(
                f"Miko did not respond ({result.reason or 'no_response'})."
            )
            return

        image_path = self._emotion_file(result.emotion_id)
        if image_path is None:
            await interaction.followup.send(result.text)
            return

        await interaction.followup.send(
            result.text,
            file=discord.File(
                image_path,
                filename=f"miko_{result.emotion_id:02d}{image_path.suffix}",
            ),
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(MikoChat(bot))
