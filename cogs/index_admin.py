from __future__ import annotations
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
    @commands.is_owner()
    async def indexstats(self, interaction: discord.Interaction) -> None:
        stats = self.manager.stats()
        body = '\n'.join(f'{key.replace("_", " ").title()}: {value:,}' for key, value in stats.items())
        await interaction.response.send_message(f'🧠 Rule43 Local Index\n{body}', ephemeral=True)

    @app_commands.command(name='sourcehealth', description='Show source health and circuit-breaker state.')
    @commands.is_owner()
    async def sourcehealth(self, interaction: discord.Interaction) -> None:
        rows = self.manager.health_rows()
        if not rows:
            await interaction.response.send_message('No source health records yet.', ephemeral=True)
            return
        lines = []
        for row in rows:
            icon = '🟢' if row.status == 'healthy' else '🟡' if row.status == 'degraded' else '🔴'
            lines.append(f'{icon} {row.source} · {row.status} · failures={row.consecutive_failures}')
        await interaction.response.send_message('\n'.join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(IndexAdminCog(bot))
