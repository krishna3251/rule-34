from __future__ import annotations
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from services.index.manager import IndexManager


class IndexAdminCog(commands.Cog):
    """Owner-only visibility into the local index and source circuit breakers."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.manager = IndexManager()

    @app_commands.command(name='indexstats', description='Show local index statistics.')
    @app_commands.check(lambda interaction: interaction.client.is_owner(interaction.user))
    async def indexstats(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        stats = await asyncio.to_thread(self.manager.stats)
        body = '\n'.join(f'{key.replace("_", " ").title()}: {value:,}' for key, value in stats.items())
        await interaction.followup.send(f'🧠 Rule43 Local Index\n{body}', ephemeral=True)

    @app_commands.command(name='sourcehealth', description='Show source health and circuit-breaker state.')
    @app_commands.check(lambda interaction: interaction.client.is_owner(interaction.user))
    async def sourcehealth(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        rows = await asyncio.to_thread(self.manager.health_rows)
        if not rows:
            await interaction.followup.send('No source health records yet.', ephemeral=True)
            return
        lines = []
        for row in rows:
            icon = '🟢' if row.status == 'healthy' else '🟡' if row.status == 'degraded' else '🔴'
            lines.append(f'{icon} {row.source} · {row.status} · failures={row.consecutive_failures}')
        await interaction.followup.send('\n'.join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(IndexAdminCog(bot))
