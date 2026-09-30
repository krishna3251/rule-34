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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not self.bot.user or self.bot.user not in message.mentions:
            return
        if message.content.strip().startswith(('/', 'n ', 'n!')):
            return
        clean = message.content.replace(f'<@{self.bot.user.id}>', '').replace(f'<@!{self.bot.user.id}>', '').strip()
        await message.reply(MikoPersonality.greeting() if not clean else MikoPersonality.reply_to(clean), mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PersonalityCog(bot))
