import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import os
import random
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


class NSFWContent(commands.Cog):
    """Combined NSFW content finder with multi-source fallback"""

    def __init__(self, bot):
        self.bot = bot
        self.session = None
        self.trending = Counter()
        self.favs = defaultdict(list)
        self.cache = {}
        self.limits = defaultdict(lambda: datetime.min)

        # API keys
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.r34_user = os.getenv("R34_USER_ID")
        self.r34_key = os.getenv("R34_API_KEY")

        # API endpoints with fallback
        self.apis = {
            "r34": ["https://api.rule34.xxx/index.php", "https://rule34.xxx/index.php"],
            "gel": ["https://gelbooru.com/index.php"],
            "dan": ["https://danbooru.donmai.us/posts.json"],
            "paheal": ["https://rule34.paheal.net/api/danbooru/find_posts/index.xml"]
        }

        # Content filters
        self.blocked = {'gay', 'yaoi', 'male_on_male', 'femboy', 'trap', 
                       'futa', 'futanari', 'dickgirl', 'shemale'}

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    def rate_check(self, uid):
        """Rate limiting"""
        now = datetime.now()
        if now - self.limits[uid] >= timedelta(seconds=2):
            self.limits[uid] = now
            return True
        return False

    def is_filtered(self, tags):
        """Check content filters"""
        tag_set = set(tags.lower().split())
        return bool(tag_set & self.blocked)

    async def parse_tags(self, query):
        """Parse query with AI fallback"""
        if self.gemini_key:
            try:
                async with self.session.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent",
                    params={"key": self.gemini_key},
                    json={"contents": [{"parts": [{"text": f"Convert to Rule34 tags: {query}"}]}]},
                    timeout=3
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        if data.get("candidates"):
                            return data["candidates"][0]["content"]["parts"][0]["text"].strip().lower()
            except:
                pass
        return query.lower().replace(" ", "_")

    async def fetch_content(self, tags="", limit=30, source="r34"):
        """Fetch with multi-source fallback"""
        # Check cache first
        cache_key = f"{source}_{tags}_{limit}"
        if cache_key in self.cache:
            cached_time, data = self.cache[cache_key]
            if (datetime.now() - cached_time).seconds < 300:
                return data

        # Try primary source
        posts = await self._fetch_from_source(tags, limit, source)
        if posts:
            self.cache[cache_key] = (datetime.now(), posts)
            return posts

        # Fallback to other sources
        for src in ["r34", "gel", "dan"]:
            if src != source:
                posts = await self._fetch_from_source(tags, limit, src)
                if posts:
                    self.cache[cache_key] = (datetime.now(), posts)
                    return posts

        # Final fallback: random explicit content
        if tags != "rating:explicit":
            return await self.fetch_content("rating:explicit", 10, "r34")
        return []

    async def _fetch_from_source(self, tags, limit, source):
        """Fetch from specific source"""
        endpoints = self.apis.get(source, self.apis["r34"])

        for endpoint in endpoints:
            try:
                params = self._build_params(tags, limit, source)
                async with self.session.get(endpoint, params=params) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)

                        # Handle different response formats
                        if source == "dan":
                            posts = data if isinstance(data, list) else []
                        else:
                            posts = data if isinstance(data, list) else data.get("post", [])

                        # Filter content
                        filtered = [p for p in posts if not self.is_filtered(p.get("tags", ""))]
                        if filtered:
                            return filtered[:limit]
            except:
                continue
        return []

    def _build_params(self, tags, limit, source):
        """Build API parameters"""
        if source == "dan":
            return {"tags": tags, "limit": limit}

        params = {
            "page": "dapi",
            "s": "post",
            "q": "index",
            "json": 1,
            "tags": tags,
            "limit": limit
        }

        if source == "r34" and self.r34_user and self.r34_key:
            params.update({"user_id": self.r34_user, "api_key": self.r34_key})

        return params

    def make_embed(self, post, title="Result", safe=False):
        """Create embed"""
        e = discord.Embed(title=f"🔞 {title}", color=0xff69b4)

        # Get image URL with fallbacks
        url = post.get("sample_url" if safe else "file_url") or \
              post.get("file_url") or post.get("image") or ""
        e.set_image(url=url)

        # Add metadata
        tags = " ".join([f"`{t}`" for t in post.get("tags", "").split()[:8]])
        e.add_field(name="Tags", value=tags or "None", inline=False)
        e.add_field(name="Score", value=post.get("score", 0), inline=True)
        e.add_field(name="ID", value=post.get("id", "?"), inline=True)

        return e

    # ===== SLASH COMMANDS =====

    @app_commands.command(name="nsfw", description="🔞 Search NSFW content")
    @app_commands.describe(q="Search query", safe="Blur mode", sort="Sort results")
    @app_commands.choices(sort=[
        app_commands.Choice(name="Random", value="random"),
        app_commands.Choice(name="Score", value="score"),
        app_commands.Choice(name="Recent", value="id")
    ])
    async def search_slash(self, inter: discord.Interaction, q: str, 
                           safe: bool = False, sort: str = "random"):
        if not inter.channel.is_nsfw():
            return await inter.response.send_message("❌ NSFW channel required!", ephemeral=True)

        if not self.rate_check(inter.user.id):
            return await inter.response.send_message("⏱️ Wait 2 seconds!", ephemeral=True)

        await inter.response.defer()

        tags = await self.parse_tags(q)
        if sort != "random":
            tags += f" sort:{sort}:desc"

        results = await self.fetch_content(tags)
        if not results:
            return await inter.followup.send(f"❌ No results for: **{q}**")

        self.trending[q] += 1
        post = random.choice(results)

        view = NavView(self, results, q, inter.user.id, safe)
        await inter.followup.send(embed=self.make_embed(post, q, safe), view=view)

    @app_commands.command(name="random", description="🎲 Random content")
    @app_commands.describe(min_score="Minimum score filter")
    async def random_slash(self, inter: discord.Interaction, min_score: int = 50):
        if not inter.channel.is_nsfw():
            return await inter.response.send_message("❌ NSFW only!", ephemeral=True)

        await inter.response.defer()
        results = await self.fetch_content(f"sort:random score:>{min_score}")

        if results:
            await inter.followup.send(embed=self.make_embed(random.choice(results), "Random"))
        else:
            await inter.followup.send("❌ Failed to fetch")

    @app_commands.command(name="trending", description="🔥 Top searches")
    async def trending_slash(self, inter: discord.Interaction):
        if not self.trending:
            return await inter.response.send_message("📉 No data yet", ephemeral=True)

        e = discord.Embed(title="🔥 Trending", color=0xff0000)
        for i, (tag, cnt) in enumerate(self.trending.most_common(6), 1):
            medal = ['🥇','🥈','🥉'][i-1] if i <= 3 else f'{i}.'
            e.add_field(name=f"{medal} {tag}", value=f"**{cnt}** searches", inline=True)

        await inter.response.send_message(embed=e)

    @app_commands.command(name="fav", description="⭐ Manage favorites")
    @app_commands.choices(action=[
        app_commands.Choice(name="View", value="view"),
        app_commands.Choice(name="Clear", value="clear")
    ])
    async def favorite_slash(self, inter: discord.Interaction, action: str):
        uid = inter.user.id

        if action == "view":
            if not self.favs[uid]:
                return await inter.response.send_message("❌ No favorites", ephemeral=True)

            e = discord.Embed(title="⭐ Your Favorites", color=0xffcc00)
            for i, url in enumerate(self.favs[uid][:6], 1):
                e.add_field(name=f"#{i}", value=f"[View]({url})", inline=True)

            await inter.response.send_message(embed=e, ephemeral=True)
        else:
            cnt = len(self.favs[uid])
            self.favs[uid].clear()
            await inter.response.send_message(f"🗑️ Cleared {cnt} favorites", ephemeral=True)

    # ===== PREFIX COMMANDS =====

    @commands.command(name='r34', aliases=['rule34'])
    async def search_cmd(self, ctx, *, tags=""):
        """Search with tags"""
        await self._send_post(ctx, tags)

    @commands.command(name='r34random')
    async def random_cmd(self, ctx):
        """Random post"""
        await self._send_post(ctx, "rating:explicit sort:random")

    @commands.command(name='r34girl')
    async def girl_cmd(self, ctx):
        """Female content"""
        await self._send_post(ctx, "rating:explicit 1girl solo")

    @commands.command(name='r34anime')
    async def anime_cmd(self, ctx):
        """Anime content"""
        await self._send_post(ctx, "rating:explicit anime")

    @commands.command(name='r34milf')
    async def milf_cmd(self, ctx):
        """MILF content"""
        await self._send_post(ctx, "rating:explicit milf")

    @commands.command(name='r34hentai')
    async def hentai_cmd(self, ctx):
        """Hentai content"""
        await self._send_post(ctx, "rating:explicit hentai")

    @commands.command(name='r34furry')
    async def furry_cmd(self, ctx):
        """Furry content"""
        await self._send_post(ctx, "rating:explicit furry")

    @commands.command(name='r34help')
    async def help_cmd(self, ctx):
        """Help command"""
        e = discord.Embed(title="🔞 NSFW Bot Commands", color=0x8B00FF)

        slash_cmds = [
            "`/nsfw [query]` - Search content",
            "`/random` - Random post",
            "`/trending` - View trending",
            "`/fav` - Manage favorites"
        ]

        prefix_cmds = [
            "`!r34 [tags]` - Search",
            "`!r34random` - Random",
            "`!r34girl` - Girl content",
            "`!r34anime` - Anime",
            "`!r34milf` - MILF",
            "`!r34hentai` - Hentai",
            "`!r34furry` - Furry"
        ]

        e.add_field(name="Slash Commands", value="\n".join(slash_cmds), inline=False)
        e.add_field(name="Prefix Commands", value="\n".join(prefix_cmds), inline=False)
        e.add_field(name="⚠️ Note", value="NSFW channels only!", inline=False)

        await ctx.send(embed=e)

    async def _send_post(self, ctx, tags=""):
        """Unified post sender for prefix commands"""
        if not getattr(ctx.channel, 'nsfw', False):
            return await ctx.send(
                embed=discord.Embed(
                    title="❌ NSFW Only",
                    description="Use in NSFW channels only.",
                    color=0xFF0000
                )
            )

        async with ctx.typing():
            posts = await self.fetch_content(tags or "rating:explicit")
            if not posts:
                return await ctx.send(
                    embed=discord.Embed(
                        title="❌ No Results",
                        description="No posts found. Try different tags.",
                        color=0xFF0000
                    )
                )

            await ctx.send(embed=self.make_embed(random.choice(posts)))

    # Error handler
    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="Missing required permissions.",
                    color=0xFF0000
                )
            )
        else:
            await ctx.send(
                embed=discord.Embed(
                    title="❌ Error",
                    description="Something went wrong. Try again later.",
                    color=0xFF0000
                )
            )


class NavView(discord.ui.View):
    """Navigation view for browsing results"""

    def __init__(self, cog, results, query, uid, safe=False):
        super().__init__(timeout=60)
        self.cog = cog
        self.results = results[:20]
        self.i = 0
        self.q = query
        self.uid = uid
        self.safe = safe
        self.update_btns()

    def update_btns(self):
        for item in self.children:
            if hasattr(item, 'custom_id'):
                if item.custom_id == "prev":
                    item.disabled = self.i == 0
                elif item.custom_id == "next":
                    item.disabled = self.i >= len(self.results) - 1

    async def update(self, inter):
        post = self.results[self.i]
        e = self.cog.make_embed(post, self.q, self.safe)
        e.set_footer(text=f"{self.i+1}/{len(self.results)}")
        self.update_btns()
        await inter.response.edit_message(embed=e, view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary, custom_id="prev")
    async def prev(self, inter: discord.Interaction, btn):
        if inter.user.id != self.uid:
            return await inter.response.send_message("❌", ephemeral=True)
        self.i = max(0, self.i - 1)
        await self.update(inter)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary, custom_id="next")
    async def next(self, inter: discord.Interaction, btn):
        if inter.user.id != self.uid:
            return await inter.response.send_message("❌", ephemeral=True)
        self.i = min(len(self.results) - 1, self.i + 1)
        await self.update(inter)

    @discord.ui.button(label="⭐", style=discord.ButtonStyle.success)
    async def fav(self, inter: discord.Interaction, btn):
        if inter.user.id != self.uid:
            return await inter.response.send_message("❌", ephemeral=True)

        url = self.results[self.i].get("file_url", "")
        if url and url not in self.cog.favs[self.uid]:
            self.cog.favs[self.uid].append(url)
            await inter.response.send_message("⭐ Saved!", ephemeral=True)
        else:
            await inter.response.send_message("Already saved", ephemeral=True)

    @discord.ui.button(label="🔄", style=discord.ButtonStyle.secondary)
    async def shuffle(self, inter: discord.Interaction, btn):
        if inter.user.id != self.uid:
            return await inter.response.send_message("❌", ephemeral=True)
        self.i = random.randint(0, len(self.results) - 1)
        await self.update(inter)

    @discord.ui.button(label="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, inter: discord.Interaction, btn):
        if inter.user.id != self.uid:
            return await inter.response.send_message("❌", ephemeral=True)
        await inter.response.defer()
        await inter.delete_original_response()


async def setup(bot):
    await bot.add_cog(NSFWContent(bot))