from __future__ import annotations

import json
import logging
from pathlib import Path

import discord
from discord.ext import commands

from services.miko_orchestrator import MikoOrchestrator

logger = logging.getLogger("discord_bot")


class MikoChat(commands.Cog):
    """Discord adapter for the Miko orchestration system."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_file = Path("data/miko_chat.json")
        self.data_file.parent.mkdir(parents=True, exist_ok=True)

        self.chat_channels: dict[int, int] = {}
        self.disabled_channels: set[int] = set()
        self.quiet_channels: set[int] = set()
        self.channel_levels: dict[int, int] = {}
        self.orchestrator = MikoOrchestrator()

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

    async def _handle_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        auto_chat = (
            bool(message.guild)
            and self.chat_channels.get(message.guild.id) == message.channel.id
        )
        channel_id = message.channel.id

        result = await self.orchestrator.handle(
            message,
            self.bot,
            auto_chat=auto_chat,
            admin_level=self.channel_levels.get(channel_id, 1),
            disabled=channel_id in self.disabled_channels,
            quiet=channel_id in self.quiet_channels,
        )

        if result.replied and result.text:
            await message.reply(result.text, mention_author=False)

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

        candidate = await self.orchestrator.is_candidate(
            message,
            self.bot,
            auto_chat=auto_chat,
            quiet=quiet,
        )
        if disabled or not candidate:
            return

        async with message.channel.typing():
            result = await self.orchestrator.handle(
                message,
                self.bot,
                auto_chat=auto_chat,
                admin_level=self.channel_levels.get(channel_id, 1),
                disabled=disabled,
                quiet=quiet,
            )

        if result.replied and result.text:
            await message.reply(result.text, mention_author=False)

    @commands.command(name="mikosetchat")
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
            "Groq connected"
            if self.orchestrator.ai_ready
            else "GROQ_API_KEY missing"
        )
        await ctx.send(f"Miko auto-chat set to {channel.mention}. {status}")

    @commands.command(name="mikounsetchat")
    @commands.has_permissions(administrator=True)
    async def miko_unset_chat(self, ctx: commands.Context) -> None:
        removed = self.chat_channels.pop(ctx.guild.id, None)
        self.save_data()
        await ctx.send(
            "Miko auto-chat removed."
            if removed
            else "No Miko auto-chat channel was configured."
        )

    @commands.command(name="mikosetlevel")
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

    @commands.command(name="mikodisabled")
    @commands.has_permissions(administrator=True)
    async def miko_disabled(self, ctx: commands.Context) -> None:
        self.disabled_channels.add(ctx.channel.id)
        self.save_data()
        await ctx.send("Miko is disabled in this channel.")

    @commands.command(name="mikoenabled")
    @commands.has_permissions(administrator=True)
    async def miko_enabled(self, ctx: commands.Context) -> None:
        self.disabled_channels.discard(ctx.channel.id)
        self.save_data()
        await ctx.send("Miko is enabled in this channel.")

    @commands.command(name="mikoquiet")
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

    @commands.command(name="mikochatinfo")
    async def miko_chat_info(self, ctx: commands.Context) -> None:
        channel_id = self.chat_channels.get(ctx.guild.id)
        channel = ctx.guild.get_channel(channel_id) if channel_id else None

        level = self.channel_levels.get(ctx.channel.id, 1)
        disabled = "yes" if ctx.channel.id in self.disabled_channels else "no"
        quiet = "yes" if ctx.channel.id in self.quiet_channels else "no"

        await ctx.send(
            "Miko Chat\n"
            f"Auto-chat: {channel.mention if channel else 'Not configured'}\n"
            f"AI: {'Groq connected' if self.orchestrator.ai_ready else 'Groq not configured'}\n"
            f"Model: {self.orchestrator.model}\n"
            "Memory: 30 min / last 10 messages\n"
            f"Channel level: {level}\n"
            f"Disabled: {disabled}\n"
            f"Quiet mode: {quiet}"
        )

    @commands.command(name="resetmiko", aliases=["resetmemory", "resetconvo"])
    async def reset_miko(self, ctx: commands.Context) -> None:
        self.orchestrator.reset_memory(
            ctx.author.id,
            getattr(ctx.guild, "id", None),
            ctx.channel.id,
        )
        await ctx.send("Miko memory cleared for you.")

    @commands.command(name="forgetmiko", aliases=["forgetme"])
    async def forget_miko(self, ctx: commands.Context) -> None:
        self.orchestrator.forget_user(ctx.author.id)
        await ctx.send(
            "Your stored Miko memory and profile have been deleted."
        )

    @commands.command(name="stopflirting")
    async def stop_flirting(self, ctx: commands.Context) -> None:
        self.orchestrator.profile.set(ctx.author.id, "level_cap", 0)
        await ctx.send("Understood. Miko will keep things clean for you.")

    @commands.command(name="allowflirting")
    async def allow_flirting(self, ctx: commands.Context) -> None:
        self.orchestrator.profile.set(ctx.author.id, "level_cap", 2)
        await ctx.send("Your personal Miko level cap has been restored.")

    @commands.command(name="askmiko", aliases=["mikoask"])
    async def ask_miko(
        self,
        ctx: commands.Context,
        *,
        question: str,
    ) -> None:
        history = self.orchestrator.memory.history(
            ctx.author.id,
            getattr(ctx.guild, "id", None),
            ctx.channel.id,
        )
        intent = self.orchestrator.router.classify(question)
        profile = self.orchestrator.profile.get(ctx.author.id)

        level = min(
            2,
            int(profile.get("level_cap", 2) or 2),
            self.channel_levels.get(ctx.channel.id, 1),
        )

        messages = self.orchestrator.context.build(
            prompt=question,
            history=history,
            spice_level=level,
            mood="serious" if intent == "serious" else "playful",
            intent=intent,
            profile=profile,
        )

        self.orchestrator.memory.add(
            ctx.author.id,
            "user",
            question,
            getattr(ctx.guild, "id", None),
            ctx.channel.id,
        )

        async with ctx.typing():
            await self.orchestrator.gate.acquire_ai_slot()
            try:
                reply = await self.orchestrator.ai.generate(messages)
            finally:
                self.orchestrator.gate.release_ai_slot()

        valid, cleaned, reason = self.orchestrator.response.validate(
            ctx.author.id,
            reply,
            level,
        )
        if not valid:
            cleaned = self.orchestrator.response.fallback(reason)

        self.orchestrator.memory.add(
            ctx.author.id,
            "assistant",
            cleaned,
            getattr(ctx.guild, "id", None),
            ctx.channel.id,
        )
        await ctx.send(cleaned)


async def setup(bot: commands.Bot):
    await bot.add_cog(MikoChat(bot))
