"""Access restrictions shared by slash, prefix and agent-invoked commands."""
import asyncio

RESTRICTED_COGS = frozenset({"SearchCog", "SimilarCog", "NSFWContent", "NutakuCog", "GameCog", "RoleplaySQL", "ImageSourceCog", "Rule34DebugCog"})
EXEMPT_COMMANDS = frozenset({"setrpchannel", "clearhistory", "endroleplay"})


async def content_access(bot, guild, channel, user) -> bool:
    if guild is None or channel is None:
        return False
    parent = getattr(channel, 'parent', None)
    target = parent if parent is not None else channel
    if not getattr(target, 'is_nsfw', lambda: False)():
        return False
    db = getattr(bot, 'db', None)
    if db is None:
        return False
    return await asyncio.to_thread(db.is_user_verified, user.id, guild.id)


def restricted(command) -> bool:
    cog = getattr(command, 'cog', None) or getattr(command, 'binding', None)
    return (type(cog).__name__ in RESTRICTED_COGS
            and getattr(command, 'name', '') not in EXEMPT_COMMANDS)


def operator_only(command) -> bool:
    cog = getattr(command, 'cog', None) or getattr(command, 'binding', None)
    return type(cog).__name__ in {"CloudflareCheckerCog", "Rule34DebugCog"}


async def prefix_access(ctx) -> bool:
    if operator_only(ctx.command) and not await ctx.bot.is_owner(ctx.author):
        return False
    if not restricted(ctx.command):
        return True
    return await content_access(ctx.bot, ctx.guild, ctx.channel, ctx.author)
