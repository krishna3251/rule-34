from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict

import discord
from discord.ext import commands

from services.miko_orchestrator import MikoOrchestrator

logger = logging.getLogger("discord_bot")


class MikoChat(commands.Cog):
    """Thin Discord adapter for the Miko orchestration layer."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_file = Path("data/miko_chat.json")
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        self.chat_channels: Dict[int, int] = {}
        self.orchestrator = MikoOrchestrator()
        self.load_data()

    def load_data(self) -> None:
        try:
            if self.data_file.exists():
                raw = json.loads(self.data_file.read_text(encoding="utf-8"))
                self.chat_channels = {
                    int(k): int(v) for k, v in raw.get("chat_channels", {}).items()
                }
        except (OSError, ValueError, TypeError) as exc:
            logger.error("Could not load Miko chat config: %s", exc)
            self.chat_channels = {}

    def save_data(self) -> None:
        try:
            self.data_file.write_text(
                json.dumps(
                    {"chat_channels": {str(k): v for k, v in self.chat_channels.items()}},
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Could not save Miko chat config: %s", exc)

    async def _is_command(self, message: discord.Message) -> bool:
        try:
            ctx = await self.bot.get_context(message)
            return bool(ctx.valid)
        except Exception:
            return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        # Main.py owns command processing. The orchestrator only handles chat.
        if await self._is_command(message):
            return

        auto_chat = (
            bool(message.guild)
            and self.chat_channels.get(message.guild.id) == message.channel.id
        )

        async with message.channel.typing():
            reply = await self.orchestrator.handle(
                message,
                self.bot,
                auto_chat=auto_chat,
            )

        if not reply:
            return

        await asyncio.sleep(0.5)
        await message.reply(reply, mention_author=False)

    @commands.command(name="mikosetchat")
    @commands.has_permissions(administrator=True)
    async def miko_set_chat(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | None = None,
    ):
        channel = channel or ctx.channel
        self.chat_channels[ctx.guild.id] = channel.id
        self.save_data()
        status = (
            "✅ Groq connected"
            if self.orchestrator.ai_ready
            else "⚠️ Groq key missing"
        )
        await ctx.send(f"🌸 Miko auto-chat set to {channel.mention}. {status}")

    @commands.command(name="mikounsetchat")
    @commands.has_permissions(administrator=True)
    async def miko_unset_chat(self, ctx: commands.Context):
        removed = self.chat_channels.pop(ctx.guild.id, None)
        self.save_data()
        await ctx.send(
            "🌙 Miko auto-chat removed."
            if removed
            else "No Miko auto-chat channel was configured."
        )

    @commands.command(name="mikochatinfo")
    async def miko_chat_info(self, ctx: commands.Context):
        channel_id = self.chat_channels.get(ctx.guild.id)
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        channel_text = channel.mention if channel else "Not configured"
        provider = (
            f"Groq / {self.orchestrator.model}"
            if self.orchestrator.ai_ready
            else "Groq not configured"
        )
        await ctx.send(
            "🌸 Miko Chat\n"
            f"Channel: {channel_text}\n"
            f"Provider: {provider}\n"
            "Memory: 30 min / last 10 messages\n"
            "Mode: direct summon + optional auto-chat"
        )

    @commands.command(name="resetmiko", aliases=["resetmemory", "resetconvo"])
    async def reset_miko(self, ctx: commands.Context):
        self.orchestrator.reset_memory(
            ctx.author.id,
            getattr(ctx.guild, "id", None),
            ctx.channel.id,
        )
        await ctx.send("🧹 Memory cleared. Ara ara~ Fresh conversation, darling.")

    @commands.command(name="askmiko", aliases=["mikoask"])
    async def ask_miko(self, ctx: commands.Context, *, question: str):
        # Direct command access uses the same memory and provider services.
        history = self.orchestrator.memory.history(
            ctx.author.id,
            getattr(ctx.guild, "id", None),
            ctx.channel.id,
        )
        self.orchestrator.memory.add(
            ctx.author.id,
            "user",
            question,
            getattr(ctx.guild, "id", None),
            ctx.channel.id,
        )
        async with ctx.typing():
            reply = await self.orchestrator.ai.generate(question, history)
        self.orchestrator.memory.add(
            ctx.author.id,
            "assistant",
            reply,
            getattr(ctx.guild, "id", None),
            ctx.channel.id,
        )
        await ctx.send(reply)


async def setup(bot: commands.Bot):
    await bot.add_cog(MikoChat(bot))
