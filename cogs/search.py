from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.search.registry import build_search_engine


class SearchCog(commands.Cog):
    """Unified discovery commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.engine = build_search_engine()

    @app_commands.command(name="search", description="Search configured R34, game, manga, and anime sources.")
    @app_commands.describe(query="Title, tag, or search term")
    async def search(self, interaction: discord.Interaction, query: str) -> None:
        if len(query.strip()) < 2:
            await interaction.response.send_message("Search query must contain at least 2 characters.", ephemeral=True)
            return

        await interaction.response.defer()
        response = await self.engine.search(query, limit_per_source=5)
        if not response.results:
            detail = ""
            if response.errors:
                detail = "\nSources with errors: " + ", ".join(response.errors)
            await interaction.followup.send(f"No indexed results for {query}.{detail}")
            return

        icons = {"r34": "🔞", "game": "🎮", "manga": "📚", "anime": "🎬"}
        lines = [f"🔎 Results for: {response.query}", ""]
        for item in response.results[:20]:
            url = f" — {item.url}" if item.url else ""
            lines.append(f"{icons[item.media_type.value]} **{item.title}** · `{item.source}`{url}")
        await interaction.followup.send("\n".join(lines)[:1900])


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SearchCog(bot))
