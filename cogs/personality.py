from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from services.personality import MikoPersonality


class PersonalityCog(commands.Cog):
    """Playful, teasing personality layer."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name='mood', description='Get a mischievous shrine-maiden-style reply.')
    async def mood(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(MikoPersonality.reply_to('tease'))

    # Miko chat is handled by cogs.miko_chat using Groq and persistent memory.
    # Keep this cog command-only so mentions do not produce duplicate replies.

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PersonalityCog(bot))
