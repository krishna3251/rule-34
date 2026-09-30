from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.image_search.registry import build_image_source_engine


class ImageSourceCog(commands.Cog):
    """Find likely sources for anime/art images."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.engine = build_image_source_engine()

    @app_commands.command(name="source", description="Find the likely source of an anime or artwork image.")
    @app_commands.describe(image="Image URL or Discord CDN URL")
    async def source(self, interaction: discord.Interaction, image: str) -> None:
        if not image.startswith(("https://", "http://")):
            await interaction.response.send_message("Please provide a valid image URL.", ephemeral=True)
            return

        await interaction.response.defer()
        matches = await self.engine.search(image, limit=5)

        if not matches:
            await interaction.followup.send("🔎 No indexed source match was found.")
            return

        embed = discord.Embed(title="🔎 Image Source Results", description=f"Found {len(matches)} indexed matches.")
        for match in matches[:5]:
            similarity = f"{match.similarity:.1f}%" if match.similarity is not None else "unknown"
            value = f"**Similarity:** {similarity}"
            if match.category:
                value += f"\n**Database:** {match.category}"
            if match.artist:
                value += f"\n**Artist:** {match.artist}"
            if match.episode is not None:
                value += f"\n**Episode:** {match.episode}"
            if match.url:
                value += f"\n[Open source]({match.url})"
            embed.add_field(name=f"{match.source} · {match.title}", value=value[:1024], inline=False)

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ImageSourceCog(bot))
