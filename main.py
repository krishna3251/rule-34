import os
import sys
import ast
import sqlite3

# Force UTF-8 on Windows before anything else touches stdout/stderr
os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
if sys.platform == 'win32':
    try:
        # reconfigure() changes encoding in-place without replacing the stream object
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add bundled vendor directory so google-generativeai and aiosqlite are always found
_vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor')
if _vendor_dir not in sys.path:
    sys.path.insert(0, _vendor_dir)

import discord
import logging
import time
import asyncio
import aiohttp
import json
import io
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from discord.ext import commands, tasks
from dotenv import load_dotenv
from database import Database
from collections import deque, defaultdict
import traceback

# --- Load .env ---
load_dotenv()


# === Enhanced Logging Configuration ===
class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for console output"""

    COLORS = {
        'DEBUG': '\033[36m',  # Cyan
        'INFO': '\033[32m',  # Green
        'WARNING': '\033[33m',  # Yellow
        'ERROR': '\033[31m',  # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'  # Reset
    }

    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        record.levelname = f"{log_color}{record.levelname}{self.COLORS['RESET']}"
        return super().format(record)


# Configure logging with both file and console handlers
logger = logging.getLogger('discord_bot')
logger.setLevel(logging.DEBUG)

# Console handler with colors
console_handler = logging.StreamHandler(stream=sys.stdout)
console_handler.setLevel(logging.INFO)
console_formatter = ColoredFormatter(
    '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')
console_handler.setFormatter(console_formatter)

# File handler for detailed logs
os.makedirs('logs', exist_ok=True)
file_handler = logging.FileHandler(
    f'logs/bot_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log',
    encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(
    '%(asctime)s | %(levelname)s | %(name)s | %(funcName)s:%(lineno)d | %(message)s'
)
file_handler.setFormatter(file_formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)


# === Configuration ===
class Config:
    """Centralized configuration management"""

    TOKEN = os.getenv("DISCORD_TOKEN")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///bot.db")
    INDEX_DB_PATH = os.getenv("INDEX_DB_PATH", "rule43_index.db")
    REDIS_URL = os.getenv("REDIS_URL")

    # Bot settings
    PREFIX = commands.when_mentioned_or("n ", "n!", "natsu ", "N ")
    OWNER_IDS = [
        int(id_) for id_ in os.getenv("OWNER_IDS", "").split(",") if id_
    ]

    # Performance settings
    MAX_MESSAGES_CACHE = 1000
    COMMAND_COOLDOWN = 3  # seconds
    ERROR_WEBHOOK_URL = os.getenv("ERROR_WEBHOOK_URL")

    # Feature flags
    ENABLE_ANALYTICS = os.getenv("ENABLE_ANALYTICS", "true").lower() == "true"
    ENABLE_AUTO_BACKUP = os.getenv("ENABLE_AUTO_BACKUP",
                                   "true").lower() == "true"
    DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
    DEBUG_GUILD_ID = int(os.getenv("DEBUG_GUILD_ID", "0") or "0")

    # Rule34 API credentials
    R34_USER_ID = os.getenv("R34_USER_ID")
    R34_API_KEY = os.getenv("R34_API_KEY")

    @classmethod
    def validate(cls):
        """Validate required configuration"""
        if not cls.TOKEN:
            logger.critical(
                "DISCORD_TOKEN not found in environment variables!")
            sys.exit(1)

        if not cls.OWNER_IDS:
            logger.warning(
                "No OWNER_IDS specified. Owner-only commands will be unavailable."
            )

        logger.info("Configuration validated successfully")


# === Cache Manager ===
class CacheManager:
    """Manages various caches for improved performance"""

    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self.command_cache = deque(maxlen=max_size)
        self.user_cooldowns = defaultdict(lambda: defaultdict(float))
        self.guild_settings = {}
        self.response_cache = {}

    def add_command(self, command_data: Dict[str, Any]):
        """Add command execution data to cache"""
        self.command_cache.append({
            **command_data, 'timestamp':
            datetime.utcnow()
        })

    def get_user_cooldown(self, user_id: int, command: str) -> float:
        """Get remaining cooldown for user command"""
        last_used = self.user_cooldowns[user_id].get(command, 0)
        return max(0, Config.COMMAND_COOLDOWN - (time.time() - last_used))

    def set_user_cooldown(self, user_id: int, command: str):
        """Set cooldown for user command"""
        self.user_cooldowns[user_id][command] = time.time()

    def clear_expired_cooldowns(self):
        """Remove expired cooldowns from memory"""
        current_time = time.time()
        for user_id in list(self.user_cooldowns.keys()):
            expired_commands = [
                cmd for cmd, last_used in self.user_cooldowns[user_id].items()
                if current_time - last_used > Config.COMMAND_COOLDOWN
            ]
            for cmd in expired_commands:
                del self.user_cooldowns[user_id][cmd]

            if not self.user_cooldowns[user_id]:
                del self.user_cooldowns[user_id]


# === Analytics Manager ===
class Analytics:
    """Track and analyze bot usage"""

    def __init__(self):
        self.command_usage = defaultdict(int)
        self.error_count = defaultdict(int)
        self.guild_activity = defaultdict(lambda: defaultdict(int))
        self.response_times = deque(maxlen=1000)

    def track_command(self, command_name: str, guild_id: int,
                      response_time: float):
        """Track command usage statistics"""
        self.command_usage[command_name] += 1
        self.guild_activity[guild_id][command_name] += 1
        self.response_times.append(response_time)

    def track_error(self, error_type: str):
        """Track error occurrences"""
        self.error_count[error_type] += 1

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive statistics"""
        avg_response = sum(self.response_times) / len(
            self.response_times) if self.response_times else 0

        return {
            'total_commands':
            sum(self.command_usage.values()),
            'unique_commands':
            len(self.command_usage),
            'top_commands':
            dict(
                sorted(self.command_usage.items(),
                       key=lambda x: x[1],
                       reverse=True)[:10]),
            'total_errors':
            sum(self.error_count.values()),
            'error_types':
            dict(self.error_count),
            'avg_response_time':
            round(avg_response * 1000, 2),  # ms
            'active_guilds':
            len(self.guild_activity)
        }


# === Enhanced Bot Class ===
class NatsuBot(commands.Bot):
    """Enhanced Discord bot with advanced features"""

    def __init__(self):
        # Configure intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        intents.presences = True

        super().__init__(command_prefix=self._get_prefix,
                         intents=intents,
                         help_command=None,
                         owner_ids=set(Config.OWNER_IDS),
                         activity=discord.Activity(
                             type=discord.ActivityType.watching,
                             name="startup..."),
                         status=discord.Status.dnd)

        # Initialize managers
        self.start_time = time.time()
        self.processing_commands = set()
        self.db = Database()
        self.cache = CacheManager()
        self.analytics = Analytics() if Config.ENABLE_ANALYTICS else None
        self.session: Optional[aiohttp.ClientSession] = None

        # Performance tracking
        self.ready = False
        self.command_stats = defaultdict(lambda: {'count': 0, 'total_time': 0})

        logger.info("Bot instance initialized")

    async def _get_prefix(self, bot, message):
        """Dynamic prefix handler with guild-specific prefixes"""
        default_prefixes = Config.PREFIX(bot, message)

        if message.guild:
            # Check for custom guild prefix
            custom_prefix = self.cache.guild_settings.get(
                message.guild.id, {}).get('prefix')

            if custom_prefix:
                return commands.when_mentioned_or(custom_prefix)(bot, message)

        return default_prefixes

    async def setup_hook(self):
        """Initialize bot components during startup"""
        logger.info("Setting up bot components...")

        # Create aiohttp session
        self.session = aiohttp.ClientSession()

        # Load extensions
        await self._load_extensions()

        # Start background tasks
        self.cleanup_task.start()
        self.status_update.start()

        if Config.ENABLE_AUTO_BACKUP:
            self.auto_backup.start()

        logger.info("Bot setup completed")

    async def _load_extensions(self):
        """Load only Python modules that expose a Discord extension setup() function."""
        os.makedirs("cogs", exist_ok=True)

        loaded = []
        failed = []
        all_cogs = []

        for filename in sorted(os.listdir("cogs")):
            if not filename.endswith(".py") or filename.startswith("__"):
                continue

            path = os.path.join("cogs", filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=path)

                has_setup = any(
                    isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
                    and node.name == "setup"
                    for node in tree.body
                )
                if has_setup:
                    all_cogs.append(filename[:-3])
                else:
                    logger.debug(f"⏭️ Skipping helper module: {filename}")
            except (OSError, SyntaxError) as e:
                failed.append(filename[:-3])
                logger.error(f"❌ Cannot inspect {filename}: {e}")

        for cog_name in all_cogs:
            try:
                await self.load_extension(f"cogs.{cog_name}")
                loaded.append(cog_name)
                logger.info(f"✅ Loaded: {cog_name}")
            except Exception as e:
                failed.append(cog_name)
                logger.error(f"❌ Failed to load {cog_name}: {e}")
                if Config.DEBUG_MODE:
                    traceback.print_exc()

        logger.info(
            f"Extension loading complete: {len(loaded)} loaded, {len(failed)} failed"
        )

        if failed:
            logger.warning(f"Failed cogs: {', '.join(failed)}")

    async def on_ready(self):
        """Called when bot is fully ready"""
        if self.ready:
            logger.info("Bot reconnected")
            return

        self.ready = True
        logger.info(
            f"✅ Logged in as {self.user} (ID: {self.user.id if self.user else 'Unknown'})"
        )

        # Sync slash commands
        await self._sync_commands()

        # Update presence
        if self.user:  # Ensure bot is properly initialized
            await self.change_presence(activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.guilds)} servers | /help"),
                                       status=discord.Status.online)

        # Display guild information
        logger.info(f"Connected to {len(self.guilds)} guilds:")
        for guild in sorted(self.guilds,
                            key=lambda g: g.member_count or 0,
                            reverse=True)[:5]:
            member_count = guild.member_count or 0
            logger.info(
                f"  • {guild.name} ({guild.id}) - {member_count:,} members")

        if len(self.guilds) > 5:
            logger.info(f"  ... and {len(self.guilds) - 5} more")

    async def _sync_commands(self):
        """Sync application commands with Discord"""
        try:
            start = time.time()

            if Config.DEBUG_MODE and Config.DEBUG_GUILD_ID:
                test_guild = discord.Object(id=Config.DEBUG_GUILD_ID)
                synced = await self.tree.sync(guild=test_guild)
                logger.info(
                    f"✅ Synced {len(synced)} commands to debug guild {Config.DEBUG_GUILD_ID}"
                )
            else:
                # Global sync
                synced = await self.tree.sync()
                logger.info(f"✅ Synced {len(synced)} global commands")

            sync_time = (time.time() - start) * 1000
            logger.debug(f"Command sync took {sync_time:.2f}ms")

        except Exception as e:
            logger.error(f"❌ Failed to sync commands: {e}")

    async def on_message(self, message: discord.Message):
        """Enhanced message processing"""
        if message.author.bot:
            return

        # Check if bot is mentioned
        if self.user and self.user in message.mentions and len(
                message.content.split()) == 1:
            prefix = (await self._get_prefix(self, message))[0]
            embed = discord.Embed(
                title="👋 Hello!",
                description=
                f"My prefix is `{prefix}`\nUse `{prefix}help` or `/help` to see commands!",
                color=discord.Color.blue())
            await message.reply(embed=embed, mention_author=False)
            return

        await self.process_commands(message)

    async def process_commands(self, message: discord.Message):
        """Process commands with duplicate prevention"""
        if message.id in self.processing_commands:
            return

        self.processing_commands.add(message.id)
        try:
            ctx = await self.get_context(message)

            if ctx.valid:
                # Check cooldown
                if not await self.is_owner(message.author):
                    cooldown = self.cache.get_user_cooldown(
                        message.author.id,
                        ctx.command.name if ctx.command else 'unknown')
                    if cooldown > 0:
                        await message.add_reaction("⏱️")
                        return

                # Track command start
                start_time = time.time()

                await self.invoke(ctx)

                # Track analytics
                if self.analytics and ctx.command:
                    response_time = time.time() - start_time
                    self.analytics.track_command(
                        ctx.command.name, ctx.guild.id if ctx.guild else 0,
                        response_time)

                    # Set cooldown
                    self.cache.set_user_cooldown(message.author.id,
                                                 ctx.command.name)
        finally:
            self.processing_commands.discard(message.id)

    async def on_command_error(self, ctx: commands.Context, error: Exception):
        """Enhanced error handling"""

        # Ignore certain errors
        ignored = (commands.CommandNotFound, commands.CheckFailure)
        if isinstance(error, ignored):
            return

        # Track error
        if self.analytics:
            self.analytics.track_error(type(error).__name__)

        # Handle specific errors
        error_messages = {}

        if isinstance(error, commands.MissingRequiredArgument):
            error_messages[type(
                error)] = f"⚠️ Missing required argument: `{error.param.name}`"
        elif isinstance(error, commands.BadArgument):
            error_messages[type(error)] = "⚠️ Invalid argument provided"
        elif isinstance(error, commands.MissingPermissions):
            error_messages[type(
                error)] = "⚠️ You don't have permission to use this command"
        elif isinstance(error, commands.BotMissingPermissions):
            error_messages[type(
                error)] = "⚠️ I don't have the required permissions"
        elif isinstance(error, commands.CommandOnCooldown):
            error_messages[type(
                error
            )] = f"⏱️ Command on cooldown. Try again in {error.retry_after:.1f}s"
        elif isinstance(error, commands.NotOwner):
            error_messages[type(error)] = "🔒 This command is owner-only!"
        elif isinstance(error, commands.DisabledCommand):
            error_messages[type(
                error)] = "🚫 This command is currently disabled"

        message = error_messages.get(type(error))

        if message:
            embed = discord.Embed(description=message,
                                  color=discord.Color.red())
            await ctx.send(embed=embed, delete_after=10)
        else:
            # Log unexpected errors
            logger.error(f"Command error in {ctx.command}: {error}",
                         exc_info=True)

            if Config.DEBUG_MODE:
                # Show detailed error in debug mode
                error_trace = ''.join(
                    traceback.format_exception(type(error), error,
                                               error.__traceback__))
                embed = discord.Embed(
                    title="🔴 Debug Error",
                    description=f"```py\n{error_trace[:1900]}```",
                    color=discord.Color.red())
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(
                    description=
                    "⚠️ An unexpected error occurred. The developers have been notified.",
                    color=discord.Color.red())
                await ctx.send(embed=embed, delete_after=10)

                # Send to error webhook if configured
                if Config.ERROR_WEBHOOK_URL and self.session:
                    await self._send_error_webhook(ctx, error)

    async def _send_error_webhook(self, ctx: commands.Context,
                                  error: Exception):
        """Send error details to webhook"""
        try:
            webhook_data = {
                "embeds": [{
                    "title":
                    "Bot Error",
                    "description":
                    f"```{str(error)[:1900]}```",
                    "fields": [
                        {
                            "name": "Command",
                            "value":
                            ctx.command.name if ctx.command else "None",
                            "inline": True
                        },
                        {
                            "name": "User",
                            "value": f"{ctx.author} ({ctx.author.id})",
                            "inline": True
                        },
                        {
                            "name": "Guild",
                            "value":
                            f"{ctx.guild.name if ctx.guild else 'DM'}",
                            "inline": True
                        },
                    ],
                    "color":
                    0xFF0000,
                    "timestamp":
                    datetime.utcnow().isoformat()
                }]
            }

            if self.session and Config.ERROR_WEBHOOK_URL:
                async with self.session.post(Config.ERROR_WEBHOOK_URL,
                                             json=webhook_data) as resp:
                    if resp.status != 204:
                        logger.warning(
                            f"Error webhook returned status {resp.status}")

        except Exception as e:
            logger.error(f"Failed to send error webhook: {e}")

    @tasks.loop(minutes=5)
    async def cleanup_task(self):
        """Periodic cleanup tasks"""
        self.cache.clear_expired_cooldowns()
        logger.debug("Cleanup task executed")

    @tasks.loop(minutes=10)
    async def status_update(self):
        """Update bot status with statistics"""
        if not self.is_ready() or not self.user:
            return  # Skip if bot isn't ready

        statuses = [
            f"{len(self.guilds)} servers | /help",
            f"{len(self.users):,} users | /help",
            f"{sum(g.member_count or 0 for g in self.guilds):,} total members",
        ]

        if self.analytics:
            stats = self.analytics.get_stats()
            statuses.append(f"{stats['total_commands']:,} commands used")

        status = statuses[int(time.time() / 600) % len(statuses)]

        try:
            await self.change_presence(activity=discord.Activity(
                type=discord.ActivityType.watching, name=status))
        except Exception as e:
            logger.debug(f"Failed to update presence: {e}")

    @tasks.loop(hours=24)
    async def auto_backup(self):
        """Create a real SQLite backup of the bot database."""
        try:
            os.makedirs("backups", exist_ok=True)
            backup_path = os.path.join(
                "backups",
                f"bot_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            )
            source_path = getattr(self.db, "db_path", "bot_data.db")

            if not os.path.exists(source_path):
                logger.warning("Backup skipped: database file does not exist yet")
                return

            with sqlite3.connect(source_path) as source:
                with sqlite3.connect(backup_path) as target:
                    source.backup(target)

            logger.info(f"✅ Database backup created: {backup_path}")

            index_path = Config.INDEX_DB_PATH
            if os.path.exists(index_path):
                index_backup = os.path.join("backups", f"rule43_index_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db")
                with sqlite3.connect(index_path) as source:
                    with sqlite3.connect(index_backup) as target:
                        source.backup(target)
                logger.info(f"✅ Local index backup created: {index_backup}")
        except Exception as e:
            logger.error(f"Backup failed: {e}", exc_info=True)

    async def close(self):
        """Cleanup on bot shutdown"""
        logger.info("Shutting down bot...")

        # Close aiohttp session
        if self.session:
            await self.session.close()

        # Stop tasks
        self.cleanup_task.cancel()
        self.status_update.cancel()

        if Config.ENABLE_AUTO_BACKUP:
            self.auto_backup.cancel()

        await super().close()
        logger.info("Bot shutdown complete")

    def uptime(self) -> int:
        """Get bot uptime in seconds"""
        return int(time.time() - self.start_time)

    def format_uptime(self) -> str:
        """Get formatted uptime string"""
        uptime = self.uptime()
        days, remainder = divmod(uptime, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts = []
        if days: parts.append(f"{days}d")
        if hours: parts.append(f"{hours}h")
        if minutes: parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")

        return " ".join(parts)


# === Initialize Bot ===
Config.validate()
bot = NatsuBot()


# === Core Commands ===
@bot.command(name="ping", aliases=["latency"])
async def ping_command(ctx: commands.Context):
    """Check bot latency"""
    start = time.perf_counter()
    message = await ctx.send("Pinging...")
    end = time.perf_counter()

    api_latency = round((end - start) * 1000)
    ws_latency = round(bot.latency * 1000)

    embed = discord.Embed(title="🏓 Pong!",
                          color=discord.Color.green()
                          if ws_latency < 100 else discord.Color.yellow())
    embed.add_field(name="WebSocket", value=f"{ws_latency}ms", inline=True)
    embed.add_field(name="API", value=f"{api_latency}ms", inline=True)

    await message.edit(content=None, embed=embed)


@bot.command(name="uptime")
async def uptime_command(ctx: commands.Context):
    """Display bot uptime"""
    embed = discord.Embed(title="⏰ Bot Uptime",
                          description=f"```{bot.format_uptime()}```",
                          color=discord.Color.blue(),
                          timestamp=datetime.utcnow())
    embed.add_field(name="Started",
                    value=f"<t:{int(bot.start_time)}:R>",
                    inline=True)
    embed.set_footer(text=f"Requested by {ctx.author}")

    await ctx.send(embed=embed)


@bot.command(name="stats", aliases=["info", "botinfo"])
async def stats_command(ctx: commands.Context):
    """Display bot statistics"""
    embed = discord.Embed(title="📊 Bot Statistics",
                          color=discord.Color.blue(),
                          timestamp=datetime.utcnow())

    # General stats
    embed.add_field(name="Servers", value=f"{len(bot.guilds):,}", inline=True)
    embed.add_field(name="Users", value=f"{len(bot.users):,}", inline=True)
    embed.add_field(name="Channels",
                    value=f"{sum(len(g.channels) for g in bot.guilds):,}",
                    inline=True)

    # System stats
    embed.add_field(name="Uptime", value=bot.format_uptime(), inline=True)
    embed.add_field(name="Latency",
                    value=f"{round(bot.latency * 1000)}ms",
                    inline=True)
    embed.add_field(name="Commands", value=f"{len(bot.commands)}", inline=True)

    # Analytics
    if bot.analytics:
        stats = bot.analytics.get_stats()
        embed.add_field(name="📈 Usage",
                        value=f"Commands Used: {stats['total_commands']:,}\n"
                        f"Avg Response: {stats['avg_response_time']}ms\n"
                        f"Errors: {stats['total_errors']:,}",
                        inline=False)

    await ctx.send(embed=embed)


@bot.group(name="dev", aliases=["developer"], invoke_without_command=True)
@commands.is_owner()
async def dev_group(ctx: commands.Context):
    """Developer commands"""
    await ctx.send_help(ctx.command)


@dev_group.command(name="reload", aliases=["r"])
async def reload_command(ctx: commands.Context, *extensions: str):
    """Reload bot extensions"""
    if not extensions:
        # Reload all
        extensions = tuple(
            ext.replace("cogs.", "") for ext in bot.extensions.keys())

    results = {"✅": [], "❌": []}

    for ext_name in extensions:
        try:
            await bot.reload_extension(f"cogs.{ext_name}")
            results["✅"].append(ext_name)
        except commands.ExtensionNotLoaded:
            try:
                await bot.load_extension(f"cogs.{ext_name}")
                results["✅"].append(f"{ext_name} (loaded)")
            except Exception as e:
                results["❌"].append(f"{ext_name}: {str(e)[:30]}")
        except Exception as e:
            results["❌"].append(f"{ext_name}: {str(e)[:30]}")

    embed = discord.Embed(title="Extension Reload",
                          color=discord.Color.green())

    if results["✅"]:
        embed.add_field(name="✅ Success",
                        value="\n".join(results["✅"]) or "None",
                        inline=False)
    if results["❌"]:
        embed.add_field(name="❌ Failed",
                        value="\n".join(results["❌"]) or "None",
                        inline=False)

    await ctx.send(embed=embed)


@dev_group.command(name="sync")
async def sync_command(ctx: commands.Context, scope: str = "global"):
    """Sync slash commands"""
    async with ctx.typing():
        if scope == "guild":
            synced = await bot.tree.sync(guild=ctx.guild)
            await ctx.send(f"✅ Synced {len(synced)} commands to this guild")
        elif scope == "global":
            synced = await bot.tree.sync()
            await ctx.send(f"✅ Synced {len(synced)} commands globally")
        else:
            await ctx.send("⚠️ Invalid scope. Use 'guild' or 'global'")


@dev_group.command(name="eval")
async def eval_command(ctx: commands.Context, *, code: str):
    """Evaluate Python code"""
    if code.startswith("```") and code.endswith("```"):
        code = code[3:-3]
        if code.startswith("py\n"):
            code = code[3:]

    local_vars = {
        "bot": bot,
        "ctx": ctx,
        "discord": discord,
        "commands": commands,
        "asyncio": asyncio,
    }

    try:
        result = eval(code, {"__builtins__": __builtins__}, local_vars)
        if asyncio.iscoroutine(result):
            result = await result

        embed = discord.Embed(title="✅ Eval Success",
                              description=f"```py\n{str(result)[:1900]}```",
                              color=discord.Color.green())
    except Exception as e:
        embed = discord.Embed(title="❌ Eval Error",
                              description=f"```py\n{str(e)[:1900]}```",
                              color=discord.Color.red())

    await ctx.send(embed=embed)


@dev_group.command(name="shutdown", aliases=["stop", "exit"])
async def shutdown_command(ctx: commands.Context):
    """Shutdown the bot"""
    embed = discord.Embed(description="🔌 Shutting down...",
                          color=discord.Color.red())
    await ctx.send(embed=embed)
    await bot.close()


# === Slash Commands ===
@bot.tree.command(name="ping", description="Check bot latency")
async def ping_slash(interaction: discord.Interaction):
    ws_latency = round(bot.latency * 1000)
    color = discord.Color.green() if ws_latency < 100 else discord.Color.yellow()
    embed = discord.Embed(title="🏓 Pong!", color=color)
    embed.add_field(name="WebSocket", value=f"{ws_latency}ms", inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="uptime", description="Check how long the bot has been running")
async def uptime_slash(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⏰ Bot Uptime",
        description=f"```{bot.format_uptime()}```",
        color=discord.Color.blue())
    embed.add_field(name="Started", value=f"<t:{int(bot.start_time)}:R>", inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="stats", description="View bot statistics")
async def stats_slash(interaction: discord.Interaction):
    embed = discord.Embed(title="📊 Bot Statistics", color=discord.Color.blue(),
                          timestamp=datetime.utcnow())
    embed.add_field(name="Servers", value=f"{len(bot.guilds):,}", inline=True)
    embed.add_field(name="Users", value=f"{len(bot.users):,}", inline=True)
    embed.add_field(name="Uptime", value=bot.format_uptime(), inline=True)
    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="Cogs", value=f"{len(bot.cogs)}", inline=True)
    embed.add_field(name="Commands", value=f"{len(bot.commands)}", inline=True)
    if bot.analytics:
        stats = bot.analytics.get_stats()
        embed.add_field(
            name="📈 Usage",
            value=f"Commands: {stats['total_commands']:,}\nAvg Response: {stats['avg_response_time']}ms\nErrors: {stats['total_errors']:,}",
            inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="help", description="View bot commands")
async def help_slash(interaction: discord.Interaction):
    """Slash command version of help"""
    embed = discord.Embed(title="📚 Bot Commands",
                          description="Here are the available commands:",
                          color=discord.Color.blue())

    # Group commands by cog
    for cog_name, cog in bot.cogs.items():
        commands_list = cog.get_commands()
        if commands_list:
            command_names = [
                f"`{cmd.name}`" for cmd in commands_list if not cmd.hidden
            ]
            if command_names:
                embed.add_field(name=cog_name,
                                value=" ".join(command_names),
                                inline=False)

    embed.set_footer(text="Use /command or prefix + command to execute")
    await interaction.response.send_message(embed=embed)


# === Run Bot ===
if __name__ == "__main__":
    try:
        logger.info("🚀 Starting NatsuBot...")
        if Config.TOKEN:
            bot.run(Config.TOKEN,
                    log_handler=None)  # We handle logging ourselves
        else:
            logger.critical(
                "No Discord token found! Please set DISCORD_TOKEN environment variable."
            )
    except KeyboardInterrupt:
        logger.info("⌨️ Bot shutdown by keyboard interrupt")
    except Exception as e:
        logger.critical(f"💀 Fatal error: {e}", exc_info=True)
        sys.exit(1)
