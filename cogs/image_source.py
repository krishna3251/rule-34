from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.image_search.registry import build_image_source_engine


class ImageSourceCog(commands.Cog):
    """Find likely indexed sources for anime/art images."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.engine = build_image_source_engine()

    @app_commands.command(name="source", description="Find the likely source of an anime or artwork image.")
    @app_commands.describe(image="Optional image attachment", image_url="Optional public image URL")
    async def source(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment | None = None,
        image_url: str | None = None,
    ) -> None:
        if image is None and not image_url:
            await interaction.response.send_message(
                "Attach an image or provide a public image URL.",
                ephemeral=True,
            )
            return

        target = image.url if image else image_url.strip()
        if not target.startswith(("https://", "http://")):
            await interaction.response.send_message("Please provide a valid image URL.", ephemeral=True)
            return

        if image and image.content_type and not image.content_type.startswith("image/"):
            await interaction.response.send_message("That attachment is not an image.", ephemeral=True)
            return

        await interaction.response.defer()
        matches = await self.engine.search(target, limit=5)

        if not matches:
            await interaction.followup.send(
                "🔎 No indexed source match was found. The image may be cropped, edited, "
                "or simply absent from the indexed databases."
            )
            return

        embed = discord.Embed(
            title="🔎 Image Source Results",
            description=f"Found {len(matches)} indexed matches.",
        )
        if image:
            embed.set_thumbnail(url=image.url)

        for match in matches[:5]:
            similarity = f"{match.similarity:.1f}%" if match.similarity is not None else "unknown"
            value = f"**Similarity:** {similarity}"
            if match.category:
                value += f"\n**Database:** {match.category}"
            if match.artist:
                value += f"\n**Artist:** {match.artist}"
            if match.episode is not None:
                value += f"\n**Episode:** {match.episode}"
            if match.timestamp is not None:
                value += f"\n**Timestamp:** {match.timestamp:.1f}s"
            if match.url:
                value += f"\n[Open source]({match.url})"
            embed.add_field(
                name=f"{match.source} · {match.title}"[:256],
                value=value[:1024],
                inline=False,
            )

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ImageSourceCog(bot))
