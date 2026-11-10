"""Private self-service controls; models cannot call these mutations."""
import io
import json

import discord
from discord import app_commands
from discord.ext import commands

from services.access_policy import content_access
from services.brain.runtime import worker
from services.brain.skills import STYLES


class MikoBrain(commands.Cog):
    brain = app_commands.Group(name='mikobrain', description='Your Miko memory, privacy and conversation controls')

    def __init__(self, bot):
        self.bot = bot

    def engine(self):
        cog = self.bot.get_cog('MikoChat')
        if cog is None:
            raise RuntimeError('Miko chat is unavailable')
        return cog.chat_engine

    @staticmethod
    def scope(interaction):
        return (interaction.guild_id or 0, interaction.channel_id, interaction.user.id)

    @brain.command(name='privacy', description='Choose persistent history and external AI for this conversation')
    async def privacy(self, interaction: discord.Interaction, history: bool = False, external_ai: bool = True):
        await interaction.response.defer(ephemeral=True)
        engine = self.engine()
        await engine.update_preferences(self.scope(interaction), history=history, external=external_ai)
        await interaction.followup.send(f'For this channel: persistent history {"on" if history else "off"}; external AI {"on" if external_ai else "off"}. Short-term context expires after 45 minutes. Approved memories require /mikobrain remember.', ephemeral=True)

    @brain.command(name='remember', description='Approve one personal memory in this user/channel/server scope')
    async def remember(self, interaction: discord.Interaction, key: app_commands.Range[str, 1, 64], value: app_commands.Range[str, 1, 1000]):
        await interaction.response.defer(ephemeral=True)
        engine = self.engine()
        await engine.mutate_user(interaction.user.id, engine.store.remember, self.scope(interaction), key, value)
        await interaction.followup.send('Saved your approved memory for this conversation.', ephemeral=True)

    @brain.command(name='memories', description='Privately view your approved memories and privacy settings')
    async def memories(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        engine = self.engine()
        facts = await worker(engine.store.facts, self.scope(interaction), '', 50)
        prefs = await worker(engine.store.preferences, self.scope(interaction))
        text = json.dumps({'preferences': prefs, 'approved_memories': facts}, ensure_ascii=False, indent=2)
        if len(text) > 1800:
            await interaction.followup.send(file=discord.File(io.BytesIO(text.encode()), filename='miko-memory.json'), ephemeral=True)
        else:
            await interaction.followup.send(text, ephemeral=True)

    @brain.command(name='forget', description='Delete an approved key, or all your memory in this channel')
    async def forget(self, interaction: discord.Interaction, key: str | None = None):
        await interaction.response.defer(ephemeral=True)
        engine = self.engine()
        await engine.forget_scope(self.scope(interaction), key)
        await interaction.followup.send('Deleted the selected memory. Privacy settings are preserved.', ephemeral=True)

    @brain.command(name='style', description='Set your preferred style; safety and consent still take precedence')
    @app_commands.choices(style=[app_commands.Choice(name=k, value=k) for k in STYLES])
    async def style(self, interaction: discord.Interaction, style: str):
        await interaction.response.defer(ephemeral=True)
        await self.engine().update_preferences(self.scope(interaction), style=style)
        await interaction.followup.send(f'Conversation style: {style}.', ephemeral=True)

    @brain.command(name='cancel', description='Cancel your active Miko request in this channel')
    async def cancel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        count = await self.engine().cancel(interaction.user.id, interaction.guild_id or 0, interaction.channel_id)
        await interaction.followup.send('Request cancelled.' if count else 'No active request here.', ephemeral=True)

    @brain.command(name='feedback', description='Mark your latest conversation helpful or unhelpful, without saving its text')
    async def feedback(self, interaction: discord.Interaction, helpful: bool):
        await interaction.response.defer(ephemeral=True)
        await worker(self.engine().store.feedback, self.scope(interaction), helpful)
        await interaction.followup.send('Feedback recorded. It does not train or modify a model.', ephemeral=True)

    @brain.command(name='knowledge', description='Administrators: save a curated knowledge entry for this channel')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def knowledge(self, interaction: discord.Interaction, title: app_commands.Range[str, 1, 64], text: app_commands.Range[str, 1, 1000]):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.guild_permissions.administrator or not await content_access(self.bot, interaction.guild, interaction.channel, interaction.user):
            await interaction.followup.send('Administrator permission, server verification and an age-restricted channel are required.', ephemeral=True)
            return
        await worker(self.engine().store.remember, (interaction.guild_id, interaction.channel_id, 0), title, text)
        await interaction.followup.send('Curated channel knowledge saved. Reuse the title to correct it.', ephemeral=True)

    @brain.command(name='status', description='Administrators: inspect bounded Brain metrics without chat text')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message('Administrator permission required.', ephemeral=True)
            return
        engine = self.engine()
        data = {'runtime': engine.runtime.snapshot(), 'provider_operations': dict(engine.ai.operations),
                'this_conversation': dict(engine.runtime.conversations.get(self.scope(interaction), {})),
                'recent_provider_latency_ms': {name: latency for name, latency in engine.ai.timings},
                'calculation_cache': {'hits': engine.skills.hits, 'misses': engine.skills.misses},
                'provider_ready': engine.ai_ready}
        await interaction.response.send_message(json.dumps(data, indent=2), ephemeral=True)

    @brain.command(name='knowledge_forget', description='Administrators: delete a curated knowledge entry in this channel')
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def knowledge_forget(self, interaction: discord.Interaction, title: app_commands.Range[str, 1, 64]):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.guild_permissions.administrator or not await content_access(self.bot, interaction.guild, interaction.channel, interaction.user):
            await interaction.followup.send('Administrator permission, server verification and an age-restricted channel are required.', ephemeral=True)
            return
        await worker(self.engine().store.forget, (interaction.guild_id, interaction.channel_id, 0), title)
        await interaction.followup.send('Curated entry deleted from this channel.', ephemeral=True)


async def setup(bot):
    await bot.add_cog(MikoBrain(bot))
