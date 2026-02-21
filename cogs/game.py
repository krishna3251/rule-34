import os, json, re, asyncio, aiosqlite, aiohttp, logging
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta
import discord
from discord.ext import commands, tasks
from discord import app_commands, Embed, Interaction
from discord.ui import View, Button
from dotenv import load_dotenv
import traceback
from urllib.parse import urlparse

# Gemini SDK (optional)
try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

load_dotenv()

# Configuration
DB_PATH = os.getenv("GAMES_DB_PATH", "games.db")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ITCH_API_KEY = os.getenv("ITCH_API_KEY")
GAME_OF_DAY_CHANNEL_ID = os.getenv("GAME_OF_DAY_CHANNEL_ID")
JSON_FILE = "games.json"
JSON_BACKUP_FILE = "games_backup.json"

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Rate limiting
USER_RATE_LIMITS = {}
RATE_LIMIT_WINDOW = 60  # seconds
MAX_REQUESTS_PER_WINDOW = 10


class GameBotError(Exception):
    """Base exception for game bot errors"""
    pass


class DatabaseError(GameBotError):
    """Database operation errors"""
    pass


class ValidationError(GameBotError):
    """Input validation errors"""
    pass


class APIError(GameBotError):
    """External API errors"""
    pass


# -------------------- Utility Functions --------------------


def validate_url(url: str) -> bool:
    """Validate URL format"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False


def sanitize_input(text: str, max_length: int = 500) -> str:
    """Sanitize and truncate input text"""
    if not text:
        return ""
    # Remove potentially harmful characters
    sanitized = re.sub(r'[<>@!]', '', text)
    return sanitized[:max_length].strip()


def check_rate_limit(user_id: int) -> bool:
    """Check if user is within rate limits"""
    now = datetime.now()
    user_requests = USER_RATE_LIMITS.get(user_id, [])

    # Remove old requests outside the window
    user_requests = [
        req_time for req_time in user_requests
        if now - req_time < timedelta(seconds=RATE_LIMIT_WINDOW)
    ]

    if len(user_requests) >= MAX_REQUESTS_PER_WINDOW:
        return False

    user_requests.append(now)
    USER_RATE_LIMITS[user_id] = user_requests
    return True


async def safe_db_operation(operation, *args, **kwargs):
    """Wrapper for safe database operations with retry logic"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return await operation(*args, **kwargs)
        except aiosqlite.Error as e:
            logger.error(f"Database error (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                raise DatabaseError(
                    f"Database operation failed after {max_retries} attempts: {str(e)}"
                )
            await asyncio.sleep(0.5 * (attempt + 1))  # Exponential backoff
        except Exception as e:
            logger.error(f"Unexpected error in database operation: {e}")
            raise DatabaseError(f"Unexpected database error: {str(e)}")


# -------------------- DB Operations --------------------


async def init_db():
    """Initialize database with proper error handling"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("PRAGMA journal_mode = WAL")

            # Games table with indexes
            await db.execute("""
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    link TEXT NOT NULL UNIQUE,
                    tags TEXT,
                    description TEXT,
                    thumbnail TEXT,
                    source TEXT DEFAULT 'manual',
                    added_by INTEGER,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Ratings table with constraints
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
                    review TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(game_id, user_id),
                    FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
                )
            """)

            # User preferences table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id INTEGER PRIMARY KEY,
                    favorite_genres TEXT,
                    notification_enabled BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes for better performance
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_games_title ON games(title)")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_games_tags ON games(tags)")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ratings_game_id ON ratings(game_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ratings_user_id ON ratings(user_id)"
            )

            await db.commit()
            logger.info("Database initialized successfully")

    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise DatabaseError(f"Database initialization failed: {str(e)}")


async def add_game_to_db(title: str,
                         link: str,
                         tags: Optional[str] = None,
                         description: Optional[str] = None,
                         thumbnail: Optional[str] = None,
                         source: str = "manual",
                         added_by: Optional[int] = None) -> int:
    """Add game to database with validation"""

    # Validate inputs
    if not title or not link:
        raise ValidationError("Title and link are required")

    if not validate_url(link):
        raise ValidationError("Invalid URL format")

    title = sanitize_input(title, 200)
    tags = sanitize_input(tags or "", 500)
    description = sanitize_input(description or "", 1000)

    async def _add_game():
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO games (title, link, tags, description, thumbnail, source, added_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (title, link, tags, description, thumbnail, source, added_by))
            await db.commit()
            return cursor.lastrowid

    return await safe_db_operation(_add_game)


async def get_games_by_criteria(title_search: Optional[str] = None,
                                tags_filter: Optional[str] = None,
                                limit: int = 30,
                                offset: int = 0) -> List[Dict[str, Any]]:
    """Get games with flexible filtering"""

    async def _get_games():
        async with aiosqlite.connect(DB_PATH) as db:
            query = "SELECT id, title, link, tags, description, thumbnail, source FROM games WHERE 1=1"
            params = []

            if title_search:
                query += " AND title LIKE ?"
                params.append(f"%{sanitize_input(title_search)}%")

            if tags_filter:
                query += " AND tags LIKE ?"
                params.append(f"%{sanitize_input(tags_filter)}%")

            query += " ORDER BY added_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            return [{
                "id": row[0],
                "title": row[1],
                "link": row[2],
                "tags": row[3],
                "description": row[4],
                "thumbnail": row[5],
                "source": row[6]
            } for row in rows]

    return await safe_db_operation(_get_games)


async def get_game_ratings(game_id: int) -> Tuple[float, int]:
    """Get average rating and count for a game"""

    async def _get_ratings():
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
                SELECT AVG(rating), COUNT(*) FROM ratings WHERE game_id = ?
            """, (game_id, ))
            row = await cursor.fetchone()
            return (float(row[0] or 0), int(row[1] or 0))

    return await safe_db_operation(_get_ratings)


# -------------------- Gemini Integration --------------------


async def generate_ai_summary(title: str, tags: List[str], desc: str) -> str:
    """Generate AI summary with proper error handling and fallback"""

    if not GEMINI_API_KEY or not HAS_GEMINI:
        # Fallback summary
        tag_str = ", ".join(tags[:3]) if tags else "indie"
        return f"Check out '{title}' - a {tag_str} game! {desc[:100]}{'...' if len(desc) > 100 else ''}"

    try:
        genai.configure(api_key=GEMINI_API_KEY)

        prompt = (
            f"Write a compelling 1-2 sentence recommendation for this game. "
            f"Be enthusiastic but concise.\n"
            f"Title: {title}\n"
            f"Genre/Tags: {', '.join(tags[:5]) if tags else 'indie game'}\n"
            f"Description: {desc[:500] if desc else 'No description available'}"
        )

        model = genai.GenerativeModel('gemini-1.5-flash')
        response = await asyncio.wait_for(asyncio.create_task(
            asyncio.to_thread(model.generate_content,
                              prompt,
                              generation_config=genai.types.GenerationConfig(
                                  max_output_tokens=120,
                                  temperature=0.8,
                              ))),
                                          timeout=10.0)

        if response and response.text:
            return response.text.strip()

    except asyncio.TimeoutError:
        logger.warning("Gemini API timeout")
    except Exception as e:
        logger.error(f"Gemini API error: {e}")

    # Fallback on any error
    tag_str = ", ".join(tags[:3]) if tags else "indie"
    return f"Try '{title}' - a {tag_str} gem! {desc[:100]}{'...' if len(desc) > 100 else ''}"


# -------------------- JSON Operations --------------------


async def export_to_json() -> int:
    """Export games to JSON with backup"""
    try:
        # Create backup if file exists
        if os.path.exists(JSON_FILE):
            os.rename(JSON_FILE, JSON_BACKUP_FILE)

        games = await get_games_by_criteria(limit=10000)

        # Add ratings to games
        for game in games:
            avg_rating, rating_count = await get_game_ratings(game['id'])
            game['average_rating'] = avg_rating
            game['rating_count'] = rating_count

        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "exported_at": datetime.now().isoformat(),
                    "games_count": len(games),
                    "games": games
                },
                f,
                indent=2,
                ensure_ascii=False)

        logger.info(f"Exported {len(games)} games to JSON")
        return len(games)

    except Exception as e:
        logger.error(f"JSON export failed: {e}")
        # Restore backup if export failed
        if os.path.exists(JSON_BACKUP_FILE):
            os.rename(JSON_BACKUP_FILE, JSON_FILE)
        raise GameBotError(f"Export failed: {str(e)}")


async def import_from_json() -> int:
    """Import games from JSON with validation"""
    if not os.path.exists(JSON_FILE):
        raise GameBotError(f"JSON file '{JSON_FILE}' not found")

    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        games = data.get("games", data) if isinstance(data, dict) else data
        imported = 0
        errors = 0

        for game in games:
            try:
                await add_game_to_db(title=game.get("title", ""),
                                     link=game.get("link", ""),
                                     tags=game.get("tags"),
                                     description=game.get("description"),
                                     thumbnail=game.get("thumbnail"),
                                     source=game.get("source", "json_import"),
                                     added_by=game.get("added_by"))
                imported += 1
            except Exception as e:
                errors += 1
                logger.warning(
                    f"Failed to import game '{game.get('title', 'Unknown')}': {e}"
                )

        logger.info(f"Imported {imported} games, {errors} errors")
        return imported

    except Exception as e:
        logger.error(f"JSON import failed: {e}")
        raise GameBotError(f"Import failed: {str(e)}")


# -------------------- UI Components --------------------


class EnhancedPaginator(View):
    """Enhanced paginator with error handling and better UX"""

    def __init__(self, embeds: List[Embed], timeout: int = 300):
        super().__init__(timeout=timeout)
        self.embeds = embeds
        self.index = 0
        self.update_buttons()

    def update_buttons(self):
        """Update button states based on current page"""
        self.prev_button.disabled = len(self.embeds) <= 1
        self.next_button.disabled = len(self.embeds) <= 1

        if len(self.embeds) > 1:
            self.prev_button.label = f"◀ {self.index + 1}/{len(self.embeds)}"
            self.next_button.label = f"{self.index + 1}/{len(self.embeds)} ▶"

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: Interaction, button: Button):
        try:
            self.index = (self.index - 1) % len(self.embeds)
            self.update_buttons()
            await interaction.response.edit_message(
                embed=self.embeds[self.index], view=self)
        except Exception as e:
            logger.error(f"Paginator prev error: {e}")
            await interaction.response.send_message(
                "❌ Navigation error occurred", ephemeral=True)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: Interaction, button: Button):
        try:
            self.index = (self.index + 1) % len(self.embeds)
            self.update_buttons()
            await interaction.response.edit_message(
                embed=self.embeds[self.index], view=self)
        except Exception as e:
            logger.error(f"Paginator next error: {e}")
            await interaction.response.send_message(
                "❌ Navigation error occurred", ephemeral=True)

    async def on_timeout(self):
        """Disable buttons on timeout"""
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except:
            pass


# -------------------- Main Cog --------------------


class GameCog(commands.Cog):
    """Enhanced Game Bot Cog with comprehensive error handling"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.startup_complete = False

    async def cog_load(self):
        """Initialize cog with proper error handling"""
        try:
            await init_db()

            # Auto-import from JSON on startup
            if os.path.exists(JSON_FILE):
                try:
                    imported = await import_from_json()
                    logger.info(
                        f"Imported {imported} games from JSON on startup")
                except Exception as e:
                    logger.error(f"Failed to import JSON on startup: {e}")

            # Start background tasks
            if not self.game_of_day_task.is_running():
                self.game_of_day_task.start()

            if not self.cleanup_task.is_running():
                self.cleanup_task.start()

            self.startup_complete = True
            logger.info("GameCog loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load GameCog: {e}")
            raise

    def cog_unload(self):
        """Cleanup on unload"""
        self.game_of_day_task.cancel()
        self.cleanup_task.cancel()
        logger.info("GameCog unloaded")

    async def cog_app_command_error(self, interaction: Interaction,
                                    error: app_commands.AppCommandError):
        """Handle slash command errors"""
        logger.error(f"Slash command error: {error}")

        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"⏱️ Command on cooldown. Try again in {error.retry_after:.1f}s",
                ephemeral=True)
        elif isinstance(error, ValidationError):
            await interaction.response.send_message(f"❌ {str(error)}",
                                                    ephemeral=True)
        elif isinstance(error, DatabaseError):
            await interaction.response.send_message(
                "❌ Database error occurred. Please try again later.",
                ephemeral=True)
        else:
            await interaction.response.send_message(
                "❌ An unexpected error occurred. Please try again.",
                ephemeral=True)

    # --- Slash command group ---
    group = app_commands.Group(name="game",
                               description="Game management commands")

    @group.command(name="add", description="Add a new game to the database")
    @app_commands.describe(title="Game title",
                           link="Game URL",
                           tags="Comma-separated tags",
                           description="Game description")
    async def add(self,
                  interaction: Interaction,
                  title: str,
                  link: str,
                  tags: Optional[str] = None,
                  description: Optional[str] = None):

        if not check_rate_limit(interaction.user.id):
            return await interaction.response.send_message(
                "⏱️ Rate limit exceeded. Please wait before adding more games.",
                ephemeral=True)

        try:
            await interaction.response.defer()

            game_id = await add_game_to_db(title=title,
                                           link=link,
                                           tags=tags,
                                           description=description,
                                           source="discord_slash",
                                           added_by=interaction.user.id)

            # Auto-update JSON export
            try:
                await export_to_json()
            except Exception as e:
                logger.warning(
                    f"Failed to auto-export JSON after game add: {e}")

            embed = Embed(
                title="✅ Game Added Successfully",
                description=f"**{title}** has been added to the database",
                color=0x00ff00)
            embed.add_field(name="ID", value=str(game_id), inline=True)
            embed.add_field(name="Tags", value=tags or "None", inline=True)
            embed.add_field(name="Link",
                            value=f"[Visit Game]({link})",
                            inline=False)

            if description:
                embed.add_field(name="Description",
                                value=description[:200],
                                inline=False)

            await interaction.followup.send(embed=embed)

        except ValidationError as e:
            await interaction.followup.send(f"❌ Validation Error: {str(e)}",
                                            ephemeral=True)
        except DatabaseError as e:
            await interaction.followup.send(
                "❌ Failed to add game. Please try again.", ephemeral=True)
        except Exception as e:
            logger.error(f"Unexpected error in add command: {e}")
            await interaction.followup.send("❌ An unexpected error occurred.",
                                            ephemeral=True)

    @group.command(name="search", description="Search for games")
    @app_commands.describe(query="Search query for game titles",
                           tags="Filter by tags")
    async def search(self,
                     interaction: Interaction,
                     query: Optional[str] = None,
                     tags: Optional[str] = None):
        try:
            await interaction.response.defer()

            games = await get_games_by_criteria(title_search=query,
                                                tags_filter=tags,
                                                limit=50)

            if not games:
                embed = Embed(
                    title="🔍 No Games Found",
                    description="No games match your search criteria.",
                    color=0xffaa00)
                return await interaction.followup.send(embed=embed)

            embeds = []
            for game in games:
                embed = Embed(title=game['title'],
                              url=game['link'],
                              color=0x0099ff)

                if game['description']:
                    embed.description = game['description'][:300]

                if game['tags']:
                    embed.add_field(name="Tags",
                                    value=game['tags'],
                                    inline=True)

                embed.add_field(name="Source",
                                value=game['source'].title(),
                                inline=True)

                # Get rating info
                try:
                    avg_rating, rating_count = await get_game_ratings(
                        game['id'])
                    if rating_count > 0:
                        embed.add_field(
                            name="Rating",
                            value=
                            f"⭐ {avg_rating:.1f}/5 ({rating_count} reviews)",
                            inline=True)
                except Exception:
                    pass

                embed.set_footer(text=f"Game ID: {game['id']}")
                embeds.append(embed)

            view = EnhancedPaginator(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

        except Exception as e:
            logger.error(f"Search command error: {e}")
            await interaction.followup.send(
                "❌ Search failed. Please try again.", ephemeral=True)

    @group.command(name="random",
                   description="Get a random game recommendation")
    async def random_game(self, interaction: Interaction):
        try:
            await interaction.response.defer()

            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("""
                    SELECT id, title, link, tags, description, thumbnail 
                    FROM games ORDER BY RANDOM() LIMIT 1
                """)
                row = await cursor.fetchone()

            if not row:
                return await interaction.followup.send(
                    "❌ No games found in database.")

            game_id, title, link, tags, description, thumbnail = row
            tags_list = tags.split(",") if tags else []

            # Generate AI summary
            ai_summary = await generate_ai_summary(title, tags_list,
                                                   description or "")

            embed = Embed(title=f"🎲 Random Game: {title}",
                          url=link,
                          description=ai_summary,
                          color=0xff6600)

            if thumbnail:
                embed.set_thumbnail(url=thumbnail)

            if tags:
                embed.add_field(name="Tags", value=tags, inline=True)

            # Get rating
            try:
                avg_rating, rating_count = await get_game_ratings(game_id)
                if rating_count > 0:
                    embed.add_field(
                        name="Community Rating",
                        value=f"⭐ {avg_rating:.1f}/5 ({rating_count} reviews)",
                        inline=True)
            except Exception:
                pass

            embed.set_footer(text="🎯 Click the title to play!")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Random game command error: {e}")
            await interaction.followup.send("❌ Failed to get random game.",
                                            ephemeral=True)

    @group.command(name="list", description="List games with pagination")
    @app_commands.describe(page="Page number (starts from 1)")
    async def list_games(self,
                         interaction: Interaction,
                         page: Optional[int] = 1):
        try:
            await interaction.response.defer()

            page = max(1, page or 1)
            per_page = 10
            offset = (page - 1) * per_page

            games = await get_games_by_criteria(limit=per_page, offset=offset)

            if not games:
                embed = Embed(
                    title="📝 No Games Found",
                    description="No games in database or page out of range.",
                    color=0xffaa00)
                return await interaction.followup.send(embed=embed)

            embeds = []
            for i, game in enumerate(games, 1):
                embed = Embed(title=f"{offset + i}. {game['title']}",
                              url=game['link'],
                              color=0x0099ff)

                if game['description']:
                    embed.description = game['description'][:200]

                if game['tags']:
                    embed.add_field(name="Tags",
                                    value=game['tags'],
                                    inline=True)

                embed.add_field(name="Source",
                                value=game['source'].title(),
                                inline=True)
                embed.set_footer(text=f"Game ID: {game['id']} • Page {page}")
                embeds.append(embed)

            view = EnhancedPaginator(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

        except Exception as e:
            logger.error(f"List command error: {e}")
            await interaction.followup.send("❌ Failed to list games.",
                                            ephemeral=True)

    @group.command(name="export", description="Export games to JSON file")
    async def export_json(self, interaction: Interaction):
        try:
            await interaction.response.defer()

            count = await export_to_json()

            embed = Embed(
                title="📦 Export Successful",
                description=f"Exported {count} games to `{JSON_FILE}`",
                color=0x00ff00)
            embed.add_field(name="File Location", value=JSON_FILE, inline=True)
            embed.add_field(name="Backup Created",
                            value=JSON_BACKUP_FILE,
                            inline=True)

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Export command error: {e}")
            await interaction.followup.send(
                "❌ Export failed. Please try again.", ephemeral=True)

    @group.command(name="import", description="Import games from JSON file")
    async def import_json(self, interaction: Interaction):
        try:
            await interaction.response.defer()

            imported = await import_from_json()

            embed = Embed(
                title="📥 Import Successful",
                description=f"Imported {imported} games from `{JSON_FILE}`",
                color=0x00ff00)
            embed.add_field(name="Source File", value=JSON_FILE, inline=True)

            await interaction.followup.send(embed=embed)

        except GameBotError as e:
            await interaction.followup.send(f"❌ Import failed: {str(e)}",
                                            ephemeral=True)
        except Exception as e:
            logger.error(f"Import command error: {e}")
            await interaction.followup.send(
                "❌ Import failed. Please try again.", ephemeral=True)

    # --- Background Tasks ---

    @tasks.loop(hours=24)
    async def game_of_day_task(self):
        """Daily game recommendation task"""
        try:
            await self.bot.wait_until_ready()

            if not GAME_OF_DAY_CHANNEL_ID:
                return

            channel = self.bot.get_channel(int(GAME_OF_DAY_CHANNEL_ID))
            if not channel:
                logger.warning(
                    f"Game of day channel {GAME_OF_DAY_CHANNEL_ID} not found")
                return

            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("""
                    SELECT title, link, tags, description, thumbnail
                    FROM games ORDER BY RANDOM() LIMIT 1
                """)
                row = await cursor.fetchone()

            if not row:
                return

            title, link, tags, description, thumbnail = row
            tags_list = tags.split(",") if tags else []

            ai_summary = await generate_ai_summary(title, tags_list,
                                                   description or "")

            embed = Embed(title=f"🌟 Game of the Day: {title}",
                          url=link,
                          description=ai_summary,
                          color=0xffd700)

            if thumbnail:
                embed.set_thumbnail(url=thumbnail)

            if tags:
                embed.add_field(name="Tags", value=tags, inline=True)

            embed.set_footer(
                text="Daily game recommendation • React with ⭐ if you like it!"
            )

            message = await channel.send(embed=embed)
            await message.add_reaction("⭐")
            await message.add_reaction("❤️")
            await message.add_reaction("🎮")

        except Exception as e:
            logger.error(f"Game of day task error: {e}")

    @tasks.loop(hours=6)
    async def cleanup_task(self):
        """Periodic cleanup task"""
        try:
            # Clean up old rate limit entries
            now = datetime.now()
            for user_id in list(USER_RATE_LIMITS.keys()):
                USER_RATE_LIMITS[user_id] = [
                    req_time for req_time in USER_RATE_LIMITS[user_id]
                    if now - req_time < timedelta(seconds=RATE_LIMIT_WINDOW)
                ]
                if not USER_RATE_LIMITS[user_id]:
                    del USER_RATE_LIMITS[user_id]

            # Database maintenance
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("VACUUM")
                await db.execute("PRAGMA optimize")
                await db.commit()

            logger.info("Cleanup task completed")

        except Exception as e:
            logger.error(f"Cleanup task error: {e}")

    @game_of_day_task.before_loop
    async def before_game_of_day(self):
        await self.bot.wait_until_ready()

    @cleanup_task.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    # --- Additional Commands ---

    @group.command(name="rate", description="Rate a game")
    @app_commands.describe(game_id="Game ID to rate",
                           rating="Rating from 1 to 5 stars",
                           review="Optional review text")
    async def rate_game(self,
                        interaction: Interaction,
                        game_id: int,
                        rating: int,
                        review: Optional[str] = None):

        if not 1 <= rating <= 5:
            return await interaction.response.send_message(
                "❌ Rating must be between 1 and 5 stars!", ephemeral=True)

        if not check_rate_limit(interaction.user.id):
            return await interaction.response.send_message(
                "⏱️ Rate limit exceeded. Please wait before rating more games.",
                ephemeral=True)

        try:
            await interaction.response.defer()

            # Check if game exists
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "SELECT title FROM games WHERE id = ?", (game_id, ))
                game_row = await cursor.fetchone()

                if not game_row:
                    return await interaction.followup.send(
                        f"❌ Game with ID {game_id} not found!", ephemeral=True)

                game_title = game_row[0]

                # Add or update rating
                review_text = sanitize_input(review or "", 500)
                await db.execute(
                    """
                    INSERT OR REPLACE INTO ratings (game_id, user_id, rating, review, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (game_id, interaction.user.id, rating, review_text))

                await db.commit()

            # Get updated rating stats
            avg_rating, rating_count = await get_game_ratings(game_id)

            embed = Embed(
                title="⭐ Rating Submitted",
                description=
                f"Your rating for **{game_title}** has been recorded!",
                color=0xffd700)
            embed.add_field(name="Your Rating",
                            value="⭐" * rating,
                            inline=True)
            embed.add_field(name="Average Rating",
                            value=f"{avg_rating:.1f}/5",
                            inline=True)
            embed.add_field(name="Total Ratings",
                            value=str(rating_count),
                            inline=True)

            if review_text:
                embed.add_field(name="Your Review",
                                value=review_text[:200],
                                inline=False)

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Rate command error: {e}")
            await interaction.followup.send("❌ Failed to submit rating.",
                                            ephemeral=True)

    @group.command(name="top", description="Show top-rated games")
    @app_commands.describe(limit="Number of games to show (max 20)")
    async def top_games(self,
                        interaction: Interaction,
                        limit: Optional[int] = 10):
        try:
            await interaction.response.defer()

            limit = max(1, min(limit or 10, 20))

            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    """
                    SELECT g.id, g.title, g.link, g.tags, AVG(r.rating) as avg_rating, COUNT(r.rating) as rating_count
                    FROM games g
                    JOIN ratings r ON g.id = r.game_id
                    GROUP BY g.id
                    HAVING rating_count >= 2
                    ORDER BY avg_rating DESC, rating_count DESC
                    LIMIT ?
                """, (limit, ))
                rows = await cursor.fetchall()

            if not rows:
                embed = Embed(
                    title="🏆 No Rated Games Yet",
                    description=
                    "No games have enough ratings to show in top list.\nGames need at least 2 ratings.",
                    color=0xffaa00)
                return await interaction.followup.send(embed=embed)

            embeds = []
            for i, (game_id, title, link, tags, avg_rating,
                    rating_count) in enumerate(rows, 1):
                rank_emoji = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"{i}."

                embed = Embed(title=f"{rank_emoji} {title}",
                              url=link,
                              color=0xffd700)

                embed.add_field(name="Rating",
                                value=f"⭐ {avg_rating:.1f}/5",
                                inline=True)
                embed.add_field(name="Reviews",
                                value=str(rating_count),
                                inline=True)

                if tags:
                    embed.add_field(name="Tags", value=tags[:100], inline=True)

                embed.set_footer(text=f"Game ID: {game_id} • Rank #{i}")
                embeds.append(embed)

            view = EnhancedPaginator(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

        except Exception as e:
            logger.error(f"Top games command error: {e}")
            await interaction.followup.send("❌ Failed to get top games.",
                                            ephemeral=True)

    @group.command(name="profile",
                   description="View your game profile and ratings")
    async def user_profile(self,
                           interaction: Interaction,
                           user: Optional[discord.Member] = None):
        try:
            await interaction.response.defer()

            target_user = user or interaction.user

            async with aiosqlite.connect(DB_PATH) as db:
                # Get user's ratings
                cursor = await db.execute(
                    """
                    SELECT g.title, g.link, r.rating, r.review, r.created_at
                    FROM ratings r
                    JOIN games g ON r.game_id = g.id
                    WHERE r.user_id = ?
                    ORDER BY r.created_at DESC
                    LIMIT 10
                """, (target_user.id, ))
                ratings = await cursor.fetchall()

                # Get user's added games count
                cursor = await db.execute(
                    """
                    SELECT COUNT(*) FROM games WHERE added_by = ?
                """, (target_user.id, ))
                games_added = (await cursor.fetchone())[0]

                # Get rating stats
                cursor = await db.execute(
                    """
                    SELECT COUNT(*), AVG(rating) FROM ratings WHERE user_id = ?
                """, (target_user.id, ))
                rating_count, avg_given_rating = await cursor.fetchone()

            embed = Embed(title=f"🎮 {target_user.display_name}'s Game Profile",
                          color=0x0099ff)

            embed.set_thumbnail(url=target_user.display_avatar.url)

            # Stats
            embed.add_field(name="Games Added",
                            value=str(games_added),
                            inline=True)
            embed.add_field(name="Games Rated",
                            value=str(rating_count or 0),
                            inline=True)
            embed.add_field(name="Avg Rating Given",
                            value=f"{avg_given_rating:.1f}/5"
                            if avg_given_rating else "N/A",
                            inline=True)

            # Recent ratings
            if ratings:
                recent_ratings = []
                for title, link, rating, review, created_at in ratings[:5]:
                    stars = "⭐" * rating
                    recent_ratings.append(f"[{title}]({link}) - {stars}")

                embed.add_field(name="Recent Ratings",
                                value="\n".join(recent_ratings),
                                inline=False)
            else:
                embed.add_field(name="Recent Ratings",
                                value="No ratings yet",
                                inline=False)

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Profile command error: {e}")
            await interaction.followup.send("❌ Failed to load profile.",
                                            ephemeral=True)

    @group.command(name="suggest",
                   description="Get personalized game suggestions")
    @app_commands.describe(genre="Preferred genre or tag")
    async def suggest_games(self,
                            interaction: Interaction,
                            genre: Optional[str] = None):
        try:
            await interaction.response.defer()

            # Get user's rating history for personalization
            async with aiosqlite.connect(DB_PATH) as db:
                if genre:
                    # Filter by genre
                    cursor = await db.execute(
                        """
                        SELECT g.id, g.title, g.link, g.tags, g.description,
                               AVG(r.rating) as avg_rating, COUNT(r.rating) as rating_count
                        FROM games g
                        LEFT JOIN ratings r ON g.id = r.game_id
                        WHERE g.tags LIKE ?
                        GROUP BY g.id
                        ORDER BY avg_rating DESC, rating_count DESC, RANDOM()
                        LIMIT 5
                    """, (f"%{sanitize_input(genre)}%", ))
                else:
                    # Get highly rated games user hasn't rated
                    cursor = await db.execute(
                        """
                        SELECT g.id, g.title, g.link, g.tags, g.description,
                               AVG(r.rating) as avg_rating, COUNT(r.rating) as rating_count
                        FROM games g
                        LEFT JOIN ratings r ON g.id = r.game_id
                        WHERE g.id NOT IN (
                            SELECT game_id FROM ratings WHERE user_id = ?
                        )
                        GROUP BY g.id
                        HAVING rating_count > 0
                        ORDER BY avg_rating DESC, RANDOM()
                        LIMIT 5
                    """, (interaction.user.id, ))

                rows = await cursor.fetchall()

            if not rows:
                embed = Embed(
                    title="🤔 No Suggestions Available",
                    description=
                    "Try adding some games or rating existing ones first!",
                    color=0xffaa00)
                return await interaction.followup.send(embed=embed)

            embeds = []
            for game_id, title, link, tags, description, avg_rating, rating_count in rows:
                embed = Embed(title=f"💡 Suggested: {title}",
                              url=link,
                              color=0x9932cc)

                if description:
                    embed.description = description[:200]

                if tags:
                    embed.add_field(name="Tags", value=tags, inline=True)

                if rating_count and avg_rating:
                    embed.add_field(
                        name="Rating",
                        value=f"⭐ {avg_rating:.1f}/5 ({rating_count} reviews)",
                        inline=True)

                embed.set_footer(
                    text=
                    f"Game ID: {game_id} • Use /game rate to rate this game!")
                embeds.append(embed)

            view = EnhancedPaginator(embeds)
            embed_title = f"🎯 Game Suggestions" + (f" for '{genre}'"
                                                   if genre else "")
            embeds[0].title = f"{embed_title}\n{embeds[0].title}"

            await interaction.followup.send(embed=embeds[0], view=view)

        except Exception as e:
            logger.error(f"Suggest command error: {e}")
            await interaction.followup.send(
                "❌ Failed to generate suggestions.", ephemeral=True)

    @group.command(name="stats", description="Show database statistics")
    async def show_stats(self, interaction: Interaction):
        try:
            await interaction.response.defer()

            async with aiosqlite.connect(DB_PATH) as db:
                # Basic stats
                cursor = await db.execute("SELECT COUNT(*) FROM games")
                total_games = (await cursor.fetchone())[0]

                cursor = await db.execute("SELECT COUNT(*) FROM ratings")
                total_ratings = (await cursor.fetchone())[0]

                cursor = await db.execute(
                    "SELECT COUNT(DISTINCT user_id) FROM ratings")
                active_users = (await cursor.fetchone())[0]

                cursor = await db.execute("SELECT AVG(rating) FROM ratings")
                avg_rating = (await cursor.fetchone())[0]

                # Top tags
                cursor = await db.execute("""
                    SELECT tags FROM games WHERE tags IS NOT NULL AND tags != ''
                """)
                all_tags = await cursor.fetchall()

                # Count tag frequency
                tag_counts = {}
                for (tags_str, ) in all_tags:
                    if tags_str:
                        for tag in tags_str.split(','):
                            tag = tag.strip().lower()
                            if tag:
                                tag_counts[tag] = tag_counts.get(tag, 0) + 1

                top_tags = sorted(tag_counts.items(),
                                  key=lambda x: x[1],
                                  reverse=True)[:5]

            embed = Embed(title="📊 Database Statistics", color=0x00ff99)

            embed.add_field(name="Total Games",
                            value=str(total_games),
                            inline=True)
            embed.add_field(name="Total Ratings",
                            value=str(total_ratings),
                            inline=True)
            embed.add_field(name="Active Users",
                            value=str(active_users),
                            inline=True)

            if avg_rating:
                embed.add_field(name="Average Rating",
                                value=f"{avg_rating:.1f}/5 ⭐",
                                inline=True)

            if top_tags:
                tag_list = [
                    f"{tag.title()}: {count}" for tag, count in top_tags
                ]
                embed.add_field(name="Popular Tags",
                                value="\n".join(tag_list),
                                inline=False)

            embed.set_footer(text="Statistics updated in real-time")
            embed.timestamp = datetime.now()

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Stats command error: {e}")
            await interaction.followup.send("❌ Failed to get statistics.",
                                            ephemeral=True)


# -------------------- Setup Function --------------------


async def setup(bot: commands.Bot):
    """Setup function for loading the cog"""
    try:
        await bot.add_cog(GameCog(bot))
        logger.info("GameCog setup completed successfully")
    except Exception as e:
        logger.error(f"Failed to setup GameCog: {e}")
        raise
