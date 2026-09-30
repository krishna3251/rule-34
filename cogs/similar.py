from __future__ import annotations

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from services.index.fingerprints import fingerprint
from services.index.manager import IndexManager


class SimilarCog(commands.Cog):
    """Find locally indexed perceptual image matches."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.manager = IndexManager()

    @app_commands.command(name='similar', description='Find similar images from the local Rule43 index.')
    @app_commands.describe(image='Image to compare against the local index.')
    async def similar(self, interaction: discord.Interaction, image: discord.Attachment) -> None:
        if not image.content_type or not image.content_type.startswith('image/'):
            await interaction.response.send_message('Please attach an image file.', ephemeral=True)
            return
        await interaction.response.defer()
        try:
            session = getattr(self.bot, 'session', None)
            if not session:
                raise RuntimeError('HTTP session is unavailable')
            async with session.get(image.url, timeout=aiohttp.ClientTimeout(total=15), headers={'User-Agent': 'Rule43/1.0'}) as response:
                if response.status != 200:
                    raise RuntimeError(f'image download returned HTTP {response.status}')
                data = await response.content.read(8 * 1024 * 1024 + 1)
                if len(data) > 8 * 1024 * 1024:
                    raise RuntimeError('image is larger than the 8 MB analysis limit')
        except Exception as exc:
            await interaction.followup.send(f'Could not analyze that image: {exc}')
            return
        info = fingerprint(data)
        phash = info.get('phash')
        if not phash:
            await interaction.followup.send('I could not calculate a perceptual fingerprint for that image.')
            return
        matches = self.manager.index.find_similar_images(phash, limit=8, max_distance=48)
        if not matches:
            await interaction.followup.send('🧬 No similar images are currently stored in the local index.')
            return
        lines = ['🧬 Local similarity matches:']
        for item in matches:
            similarity = max(0, 100 - (item['distance'] / 256 * 100))
            lines.append(f"{similarity:.1f}% · {item['sha256'][:16]} · {item['width'] or '?'}x{item['height'] or '?'}")
        await interaction.followup.send('\n'.join(lines))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SimilarCog(bot))
