# nutaku_cog.py
import aiohttp, random, os, re, aiosqlite, asyncio, logging
from typing import List, Dict, Optional
import discord
from discord.ext import commands
from discord import app_commands, Interaction, Embed, ButtonStyle
from dotenv import load_dotenv
from .game import export_to_json, generate_ai_summary  # reuse helpers

load_dotenv()
DB_PATH = os.getenv("GAMES_DB_PATH", "games.db")

# Setup logging
logger = logging.getLogger(__name__)


# ---------------- Pagination View ----------------
class Paginator(discord.ui.View):

    def __init__(self, embeds: List[Embed]):
        super().__init__(timeout=300)  # Increased timeout
        self.embeds = embeds
        self.index = 0

        # Update button states
        self._update_buttons()

    def _update_buttons(self):
        """Update button states based on current position"""
        if len(self.embeds) <= 1:
            # Hide buttons if only one page
            for item in self.children:
                item.disabled = True
        else:
            # Update labels with page info
            for item in self.children:
                if hasattr(item, 'custom_id'):
                    if item.custom_id == 'prev':
                        item.label = f"⬅ Prev ({self.index + 1}/{len(self.embeds)})"
                    elif item.custom_id == 'next':
                        item.label = f"Next ➡ ({self.index + 1}/{len(self.embeds)})"

    @discord.ui.button(label="⬅ Prev",
                       style=ButtonStyle.secondary,
                       custom_id="prev")
    async def prev(self, interaction: Interaction, button: discord.ui.Button):
        try:
            self.index = (self.index - 1) % len(self.embeds)
            self._update_buttons()
            await interaction.response.edit_message(
                embed=self.embeds[self.index], view=self)
        except Exception as e:
            logger.error(f"Error in pagination prev: {e}")
            await interaction.response.send_message(
                "❌ Error navigating pages.", ephemeral=True)

    @discord.ui.button(label="Next ➡",
                       style=ButtonStyle.secondary,
                       custom_id="next")
    async def next(self, interaction: Interaction, button: discord.ui.Button):
        try:
            self.index = (self.index + 1) % len(self.embeds)
            self._update_buttons()
            await interaction.response.edit_message(
                embed=self.embeds[self.index], view=self)
        except Exception as e:
            logger.error(f"Error in pagination next: {e}")
            await interaction.response.send_message(
                "❌ Error navigating pages.", ephemeral=True)

    @discord.ui.button(label="🗑️ Close",
                       style=ButtonStyle.danger,
                       custom_id="close")
    async def close(self, interaction: Interaction, button: discord.ui.Button):
        try:
            await interaction.response.edit_message(view=None)
            self.stop()
        except Exception as e:
            logger.error(f"Error closing pagination: {e}")

    async def on_timeout(self):
        """Called when the view times out"""
        try:
            # Disable all buttons when timeout occurs
            for item in self.children:
                item.disabled = True
        except Exception as e:
            logger.error(f"Error in pagination timeout: {e}")


# ---------------- Nutaku Cog ----------------
class NutakuCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self._session_timeout = aiohttp.ClientTimeout(total=30, connect=10)

    async def cog_load(self):
        """Initialize the HTTP session when cog loads"""
        self.session = aiohttp.ClientSession(
            timeout=self._session_timeout,
            headers={
                'User-Agent':
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            })

    async def cog_unload(self):
        """Clean up HTTP session when cog unloads"""
        if self.session and not self.session.closed:
            await self.session.close()

    group = app_commands.Group(name="nutaku", description="Nutaku NSFW games")

    # --- Enhanced NSFW check ---
    async def ensure_nsfw(self, interaction: Interaction) -> bool:
        """Enhanced NSFW channel verification with better error handling"""
        try:
            # Check if it's a DM
            if isinstance(interaction.channel, discord.DMChannel):
                await interaction.response.send_message(
                    "⚠️ NSFW commands are not available in DMs for safety reasons.",
                    ephemeral=True)
                return False

            # Check if channel exists and is NSFW
            if not hasattr(interaction.channel,
                           'is_nsfw') or not interaction.channel.is_nsfw():
                embed = Embed(
                    title="🔞 NSFW Channel Required",
                    description=
                    "This command can only be used in NSFW-marked channels.",
                    color=0xff6b6b)
                embed.add_field(
                    name="How to enable NSFW:",
                    value=
                    "• Go to channel settings\n• Enable 'Age-Restricted Channel (NSFW)'\n• Try the command again",
                    inline=False)
                await interaction.response.send_message(embed=embed,
                                                        ephemeral=True)
                return False

            return True

        except Exception as e:
            logger.error(f"Error in NSFW check: {e}")
            await interaction.response.send_message(
                "❌ Error checking channel permissions.", ephemeral=True)
            return False

    # --- Enhanced scraping with better error handling ---
    async def fetch_games(self,
                          url: str,
                          limit: int = 5) -> List[Dict[str, str]]:
        """Fetch games with comprehensive error handling and retry logic"""
        games = []
        max_retries = 3

        if not self.session or self.session.closed:
            await self.cog_load()

        for attempt in range(max_retries):
            try:
                async with self.session.get(url) as resp:
                    if resp.status == 429:  # Rate limited
                        wait_time = 2**attempt  # Exponential backoff
                        logger.warning(
                            f"Rate limited, waiting {wait_time}s before retry {attempt + 1}"
                        )
                        await asyncio.sleep(wait_time)
                        continue

                    elif resp.status == 404:
                        logger.warning(f"Page not found: {url}")
                        return []

                    elif resp.status != 200:
                        logger.warning(f"HTTP {resp.status} for {url}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(1)
                            continue
                        return []

                    html = await resp.text()

                    # Multiple regex patterns for better coverage
                    patterns = [
                        r'<a href="(https://www\.nutaku\.net/games/[^"]+)"[^>]*>.*?<[^>]*>([^<]+)</[^>]*>.*?</a>',
                        r'<a href="(https://www\.nutaku\.net/games/[^"]+)"[^>]*>(.*?)</a>',
                        r'href="(https://www\.nutaku\.net/games/[^"]+)"[^>]*title="([^"]+)"'
                    ]

                    seen_links = set()

                    for pattern in patterns:
                        matches = re.findall(pattern, html,
                                             re.IGNORECASE | re.DOTALL)

                        for link, title in matches:
                            if link in seen_links or len(games) >= limit:
                                continue

                            seen_links.add(link)

                            # Clean up title
                            clean_title = re.sub(r'<[^>]+>', '', title).strip()
                            clean_title = re.sub(r'\s+', ' ', clean_title)

                            if clean_title and len(
                                    clean_title) > 2:  # Basic validation
                                games.append({
                                    "title": clean_title,
                                    "link": link,
                                    "thumbnail": None
                                })

                        if len(games) >= limit:
                            break

                    # Try to extract thumbnails if we have games
                    if games:
                        img_pattern = r'<img[^>]+src="([^"]*\.(?:jpg|jpeg|png|webp))"[^>]*alt="([^"]*)"[^>]*>'
                        img_matches = re.findall(img_pattern, html,
                                                 re.IGNORECASE)

                        # Try to match thumbnails to games by title similarity
                        for img_url, img_alt in img_matches:
                            for game in games:
                                if not game["thumbnail"] and (
                                        game["title"].lower()
                                        in img_alt.lower() or img_alt.lower()
                                        in game["title"].lower()):
                                    if img_url.startswith('//'):
                                        img_url = 'https:' + img_url
                                    elif img_url.startswith('/'):
                                        img_url = 'https://www.nutaku.net' + img_url
                                    game["thumbnail"] = img_url
                                    break

                    return games

            except aiohttp.ClientError as e:
                logger.error(f"Network error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue

            except Exception as e:
                logger.error(f"Unexpected error fetching games: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue

        logger.error(f"Failed to fetch games after {max_retries} attempts")
        return []

    # --- Enhanced search command ---
    @group.command(name="search", description="Search for Nutaku games")
    @app_commands.describe(query="Search term for games",
                           limit="Number of results (1-20, default: 5)")
    async def search(self,
                     interaction: Interaction,
                     query: str,
                     limit: int = 5):
        """Search for games with enhanced error handling"""
        if not await self.ensure_nsfw(interaction):
            return

        # Input validation
        if not query.strip():
            await interaction.response.send_message(
                "❌ Please provide a search query.", ephemeral=True)
            return

        if not 1 <= limit <= 20:
            await interaction.response.send_message(
                "❌ Limit must be between 1 and 20.", ephemeral=True)
            return

        # Defer response for longer operations
        await interaction.response.defer()

        try:
            # Sanitize query for URL
            safe_query = re.sub(r'[^\w\s-]', '',
                                query.strip())[:100]  # Limit length
            url = f"https://www.nutaku.net/games/search/?q={safe_query}"

            games = await self.fetch_games(url, limit)

            if not games:
                embed = Embed(title="🔍 No Results Found",
                              description=f"No games found for **{query}**",
                              color=0x95a5a6)
                embed.add_field(
                    name="Suggestions:",
                    value=
                    "• Try different keywords\n• Check spelling\n• Use broader terms\n• Try `/nutaku trending` for popular games",
                    inline=False)
                await interaction.followup.send(embed=embed)
                return

            # Create embeds with better error handling
            embeds = []
            for i, game in enumerate(games):
                try:
                    # Generate AI summary with error handling
                    ai_text = "Loading description..."
                    try:
                        ai_text = await generate_ai_summary(
                            game["title"], [query], "")
                    except Exception as e:
                        logger.error(
                            f"AI summary failed for {game['title']}: {e}")
                        ai_text = f"🎮 **{game['title']}** - A game found in your search results."

                    embed = Embed(
                        title=game["title"][:256],  # Discord title limit
                        url=game["link"],
                        description=ai_text[:
                                            4096],  # Discord description limit
                        color=0xe74c3c)

                    embed.set_footer(
                        text=f"Result {i+1}/{len(games)} • Nutaku")

                    if game.get("thumbnail"):
                        embed.set_image(url=game["thumbnail"])

                    embeds.append(embed)

                except Exception as e:
                    logger.error(f"Error creating embed for game: {e}")
                    continue

            if not embeds:
                await interaction.followup.send(
                    "❌ Error processing search results.")
                return

            view = Paginator(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

        except Exception as e:
            logger.error(f"Error in search command: {e}")
            await interaction.followup.send(
                "❌ An error occurred while searching. Please try again.")

    # --- Enhanced trending command ---
    @group.command(name="trending", description="Get trending Nutaku games")
    @app_commands.describe(limit="Number of results (1-20, default: 5)")
    async def trending(self, interaction: Interaction, limit: int = 5):
        """Get trending games with enhanced error handling"""
        if not await self.ensure_nsfw(interaction):
            return

        if not 1 <= limit <= 20:
            await interaction.response.send_message(
                "❌ Limit must be between 1 and 20.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            url = "https://www.nutaku.net/games/trending/"
            games = await self.fetch_games(url, limit)

            if not games:
                embed = Embed(
                    title="📈 Trending Games Unavailable",
                    description="Unable to fetch trending games at the moment.",
                    color=0x95a5a6)
                embed.add_field(
                    name="Try instead:",
                    value=
                    "• `/nutaku new` for new releases\n• `/nutaku random` for a random game\n• `/nutaku search <query>` to search",
                    inline=False)
                await interaction.followup.send(embed=embed)
                return

            embeds = await self._create_game_embeds(games, "📈 Trending")

            if not embeds:
                await interaction.followup.send(
                    "❌ Error processing trending games.")
                return

            view = Paginator(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

        except Exception as e:
            logger.error(f"Error in trending command: {e}")
            await interaction.followup.send(
                "❌ An error occurred while fetching trending games.")

    # --- Enhanced new releases command ---
    @group.command(name="new", description="Get new Nutaku game releases")
    @app_commands.describe(limit="Number of results (1-20, default: 5)")
    async def new(self, interaction: Interaction, limit: int = 5):
        """Get new releases with enhanced error handling"""
        if not await self.ensure_nsfw(interaction):
            return

        if not 1 <= limit <= 20:
            await interaction.response.send_message(
                "❌ Limit must be between 1 and 20.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            url = "https://www.nutaku.net/games/new/"
            games = await self.fetch_games(url, limit)

            if not games:
                embed = Embed(
                    title="🆕 New Releases Unavailable",
                    description="Unable to fetch new releases at the moment.",
                    color=0x95a5a6)
                await interaction.followup.send(embed=embed)
                return

            embeds = await self._create_game_embeds(games, "🆕 New Release")

            if not embeds:
                await interaction.followup.send(
                    "❌ Error processing new releases.")
                return

            view = Paginator(embeds)
            await interaction.followup.send(embed=embeds[0], view=view)

        except Exception as e:
            logger.error(f"Error in new releases command: {e}")
            await interaction.followup.send(
                "❌ An error occurred while fetching new releases.")

    # --- Enhanced random command ---
    @group.command(name="random", description="Get a random Nutaku game")
    async def random_game(self, interaction: Interaction):
        """Get random game with enhanced error handling"""
        if not await self.ensure_nsfw(interaction):
            return

        await interaction.response.defer()

        try:
            # Try multiple sources for better randomness
            urls = [
                "https://www.nutaku.net/games/trending/",
                "https://www.nutaku.net/games/new/",
                "https://www.nutaku.net/games/"
            ]

            games = []
            for url in urls:
                try:
                    games.extend(await self.fetch_games(url, limit=10))
                    if len(games) >= 20:  # Good pool for randomness
                        break
                except Exception as e:
                    logger.error(f"Error fetching from {url}: {e}")
                    continue

            if not games:
                embed = Embed(
                    title="🎲 Random Game Unavailable",
                    description=
                    "Unable to fetch games for random selection at the moment.",
                    color=0x95a5a6)
                await interaction.followup.send(embed=embed)
                return

            game = random.choice(games)

            try:
                ai_text = await generate_ai_summary(
                    game["title"], [], "random game recommendation")
            except Exception:
                ai_text = f"🎮 **{game['title']}** - A randomly selected game for you to discover!"

            embed = Embed(title=f"🎲 Random Pick: {game['title']}",
                          url=game["link"],
                          description=ai_text,
                          color=0x9b59b6)

            embed.set_footer(
                text=f"Selected from {len(games)} available games • Nutaku")

            if game.get("thumbnail"):
                embed.set_image(url=game["thumbnail"])

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in random game command: {e}")
            await interaction.followup.send(
                "❌ An error occurred while selecting a random game.")

    # --- Enhanced import command ---
    @group.command(name="import",
                   description="Import Nutaku games to database")
    @app_commands.describe(
        query="Search term for games to import",
        limit="Number of games to import (1-20, default: 5)")
    async def import_games(self,
                           interaction: Interaction,
                           query: str,
                           limit: int = 5):
        """Import games with enhanced error handling"""
        if not await self.ensure_nsfw(interaction):
            return

        # Check permissions (you might want to restrict this to certain roles)
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "❌ You need Manage Messages permission to import games.",
                ephemeral=True)
            return

        if not query.strip():
            await interaction.response.send_message(
                "❌ Please provide a search query.", ephemeral=True)
            return

        if not 1 <= limit <= 20:
            await interaction.response.send_message(
                "❌ Limit must be between 1 and 20.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            safe_query = re.sub(r'[^\w\s-]', '', query.strip())[:100]
            url = f"https://www.nutaku.net/games/search/?q={safe_query}"

            games = await self.fetch_games(url, limit)

            if not games:
                await interaction.followup.send(
                    f"❌ No games found for query: **{query}**")
                return

            # Database operations with proper error handling
            imported_count = 0
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    for game in games:
                        try:
                            await db.execute(
                                """
                                INSERT OR IGNORE INTO games (title, link, source, created_at)
                                VALUES (?, ?, ?, datetime('now'))
                            """, (game["title"], game["link"], "nutaku"))
                            imported_count += 1
                        except Exception as e:
                            logger.error(
                                f"Error importing game {game['title']}: {e}")
                            continue

                    await db.commit()

            except Exception as e:
                logger.error(f"Database error during import: {e}")
                await interaction.followup.send(
                    "❌ Database error occurred during import.")
                return

            # Export to JSON with error handling
            try:
                await export_to_json()
            except Exception as e:
                logger.error(f"Error exporting to JSON: {e}")
                # Continue anyway, database import succeeded

            embed = Embed(
                title="✅ Import Complete",
                description=f"Successfully imported **{imported_count}** games",
                color=0x2ecc71)
            embed.add_field(name="Query", value=query, inline=True)
            embed.add_field(name="Source", value="Nutaku", inline=True)
            embed.add_field(name="Total Found",
                            value=str(len(games)),
                            inline=True)

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in import command: {e}")
            await interaction.followup.send(
                "❌ An error occurred during import.")

    # --- Enhanced suggest command ---
    @group.command(name="suggest",
                   description="Get a personalized game suggestion")
    async def suggest(self, interaction: Interaction):
        """Get personalized suggestion with enhanced error handling"""
        if not await self.ensure_nsfw(interaction):
            return

        await interaction.response.defer()

        try:
            # Get user preferences with error handling
            query = None
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    cursor = await db.execute(
                        "SELECT preferred_tags FROM user_prefs WHERE user_id = ?",
                        (interaction.user.id, ))
                    row = await cursor.fetchone()
                    if row and row[0]:
                        query = row[0].split(",")[0].strip()
            except Exception as e:
                logger.error(f"Error fetching user preferences: {e}")

            # Fetch games based on preferences or trending
            games = []
            if query:
                try:
                    safe_query = re.sub(r'[^\w\s-]', '', query)[:100]
                    url = f"https://www.nutaku.net/games/search/?q={safe_query}"
                    games = await self.fetch_games(url, limit=15)
                except Exception as e:
                    logger.error(f"Error fetching personalized games: {e}")

            # Fallback to trending if no personalized results
            if not games:
                try:
                    url = "https://www.nutaku.net/games/trending/"
                    games = await self.fetch_games(url, limit=15)
                except Exception as e:
                    logger.error(
                        f"Error fetching trending games for suggestion: {e}")

            if not games:
                embed = Embed(
                    title="💡 Suggestion Unavailable",
                    description=
                    "Unable to fetch game suggestions at the moment.",
                    color=0x95a5a6)
                embed.add_field(
                    name="Try:",
                    value=
                    "• Set preferences with `/preferences set`\n• Use `/nutaku random` instead\n• Try again later",
                    inline=False)
                await interaction.followup.send(embed=embed)
                return

            game = random.choice(games)

            try:
                ai_text = await generate_ai_summary(
                    game["title"], [query] if query else [],
                    f"personalized suggestion based on {query}"
                    if query else "trending game suggestion")
            except Exception:
                preference_text = f" based on your interest in **{query}**" if query else ""
                ai_text = f"🎮 **{game['title']}** - A game suggestion{preference_text}!"

            title_prefix = "💡 Personalized Suggestion" if query else "💡 Trending Suggestion"
            embed = Embed(title=f"{title_prefix}: {game['title']}",
                          url=game["link"],
                          description=ai_text,
                          color=0x3498db)

            if query:
                embed.add_field(name="Based on your preference",
                                value=query,
                                inline=True)

            embed.set_footer(
                text=f"For {interaction.user.display_name} • Nutaku")

            if game.get("thumbnail"):
                embed.set_image(url=game["thumbnail"])

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in suggest command: {e}")
            await interaction.followup.send(
                "❌ An error occurred while generating suggestion.")

    # --- Helper method for creating embeds ---
    async def _create_game_embeds(self,
                                  games: List[Dict],
                                  prefix: str = "") -> List[Embed]:
        """Create embeds for games with error handling"""
        embeds = []

        for i, game in enumerate(games):
            try:
                ai_text = "Loading description..."
                try:
                    ai_text = await generate_ai_summary(game["title"], [], "")
                except Exception as e:
                    logger.error(f"AI summary failed for {game['title']}: {e}")
                    ai_text = f"🎮 **{game['title']}**"

                title = f"{prefix}: {game['title']}" if prefix else game[
                    'title']
                embed = Embed(title=title[:256],
                              url=game["link"],
                              description=ai_text[:4096],
                              color=0xe74c3c)

                embed.set_footer(text=f"Game {i+1}/{len(games)} • Nutaku")

                if game.get("thumbnail"):
                    embed.set_image(url=game["thumbnail"])

                embeds.append(embed)

            except Exception as e:
                logger.error(
                    f"Error creating embed for game {game.get('title', 'Unknown')}: {e}"
                )
                continue

        return embeds


async def setup(bot: commands.Bot):
    await bot.add_cog(NutakuCog(bot))
