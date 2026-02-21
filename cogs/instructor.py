import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import random
import asyncio
import logging
import sys
import json
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('rule34_debug.log', encoding='utf-8')
    ]
)


class Rule34DebugCog(commands.Cog):
    """Debug version of Rule34 cog to inspect API responses"""

    def __init__(self, bot):
        self.bot = bot
        self.session = None
        self.logger = logging.getLogger('Rule34Debug')
        self.logger.setLevel(logging.DEBUG)

        # API mirrors with correct endpoints
        self.api_mirrors = [
            {
                "name": "Rule34.xxx", 
                "url": "https://rule34.xxx/index.php",
                "params_template": {'page': 'dapi', 's': 'post', 'q': 'index', 'json': '1'}
            },
            {
                "name": "Rule34 API", 
                "url": "https://api.rule34.xxx/index.php",
                "params_template": {'page': 'dapi', 's': 'post', 'q': 'index', 'json': '1'}
            },
            {
                "name": "Gelbooru", 
                "url": "https://gelbooru.com/index.php",
                "params_template": {'page': 'dapi', 's': 'post', 'q': 'index', 'json': '1'}
            },
            {
                "name": "E621", 
                "url": "https://e621.net/posts.json",
                "params_template": {}
            }
        ]

        self.blocked_tags = ['gay', 'yaoi', 'male_on_male', 'femboy', 'trap', 'futa', 'futanari', 'dickgirl', 'shemale']

        print("🔧 RULE34 DEBUG VERSION INITIALIZING")
        self.logger.info("🔧 Rule34 Debug version initialized")

    async def cog_load(self):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),  # Longer timeout for debugging
            headers=headers
        )
        print("🚀 Rule34 Debug session created")
        self.logger.info("🚀 Rule34 Debug session initialized")

    async def cog_unload(self):
        if self.session:
            await self.session.close()
            print("🔌 Rule34 Debug session closed")

    def is_nsfw(self, channel):
        return getattr(channel, 'nsfw', False)

    def is_filtered(self, tags):
        return any(tag in tags.lower() for tag in self.blocked_tags)

    async def debug_api_response(self, mirror, tags="", limit=20):
        """Debug API response in detail"""
        mirror_name = mirror["name"]
        mirror_url = mirror["url"]

        # Build parameters
        params = mirror["params_template"].copy()
        if mirror_name == "E621":
            params.update({"limit": limit, "tags": tags})
        else:
            params.update({"tags": tags, "limit": limit})

        print(f"\n{'='*50}")
        print(f"🔍 DEBUGGING: {mirror_name}")
        print(f"🌐 URL: {mirror_url}")
        print(f"📋 Parameters: {params}")
        print(f"{'='*50}")

        try:
            async with self.session.get(mirror_url, params=params) as response:
                # Get response info
                status = response.status
                headers = dict(response.headers)
                content_type = headers.get('content-type', 'unknown')

                print(f"📊 RESPONSE STATUS: {status}")
                print(f"📄 CONTENT TYPE: {content_type}")
                print(f"📏 CONTENT LENGTH: {headers.get('content-length', 'unknown')}")

                # Get response text
                response_text = await response.text()

                print(f"📝 RESPONSE LENGTH: {len(response_text)} characters")
                print(f"📄 RESPONSE PREVIEW (first 300 chars):")
                print(f"'{response_text[:300]}{'...' if len(response_text) > 300 else ''}'")

                # Try to parse as JSON
                if status == 200:
                    try:
                        if not response_text.strip():
                            print("❌ EMPTY RESPONSE - No content returned")
                            return None

                        # Check if it looks like JSON
                        text_stripped = response_text.strip()
                        if not (text_stripped.startswith('{') or text_stripped.startswith('[')):
                            print(f"❌ NOT JSON - Response starts with: '{text_stripped[:50]}...'")
                            print("🔍 Checking if it's HTML...")
                            if '<html' in response_text.lower() or '<!doctype' in response_text.lower():
                                print("🌐 DETECTED HTML RESPONSE - API returned webpage")
                                # Look for specific error messages
                                if 'cloudflare' in response_text.lower():
                                    print("🛡️ CLOUDFLARE DETECTED in HTML response")
                                if 'access denied' in response_text.lower():
                                    print("🚫 ACCESS DENIED detected in HTML response")
                                if 'captcha' in response_text.lower():
                                    print("🧩 CAPTCHA detected in HTML response")
                            return None

                        # Parse JSON
                        data = json.loads(response_text)
                        print(f"✅ JSON PARSED SUCCESSFULLY")
                        print(f"📊 DATA TYPE: {type(data)}")

                        if isinstance(data, list):
                            print(f"📋 LIST LENGTH: {len(data)}")
                            if data:
                                print("📄 FIRST ITEM KEYS:")
                                first_item = data[0]
                                if isinstance(first_item, dict):
                                    for key, value in first_item.items():
                                        value_preview = str(value)[:50] + "..." if len(str(value)) > 50 else str(value)
                                        print(f"   {key}: {value_preview}")

                                # Filter check
                                filtered = [p for p in data if not self.is_filtered(p.get('tags', ''))]
                                print(f"🛡️ AFTER FILTERING: {len(filtered)} items (removed {len(data) - len(filtered)} filtered items)")

                                return filtered
                            else:
                                print("📭 EMPTY LIST - No posts returned")
                                return []

                        elif isinstance(data, dict):
                            print(f"📄 DICT KEYS: {list(data.keys())}")
                            # Some APIs return {"posts": [...]}
                            if 'posts' in data:
                                posts = data['posts']
                                print(f"📋 FOUND 'posts' KEY with {len(posts)} items")
                                filtered = [p for p in posts if not self.is_filtered(p.get('tags', ''))]
                                print(f"🛡️ AFTER FILTERING: {len(filtered)} items")
                                return filtered
                            # Check for error messages in dict
                            elif 'error' in data or 'message' in data:
                                print(f"❌ API ERROR MESSAGE: {data}")
                                return None
                            else:
                                print("❓ UNEXPECTED DICT FORMAT")
                                return None
                        else:
                            print(f"❓ UNEXPECTED DATA TYPE: {type(data)}")
                            return None

                    except json.JSONDecodeError as e:
                        print(f"❌ JSON DECODE ERROR: {e}")
                        print("📄 RAW RESPONSE (first 500 chars):")
                        print(f"'{response_text[:500]}'")
                        return None
                else:
                    print(f"❌ HTTP ERROR {status}")
                    print("📄 ERROR RESPONSE:")
                    print(f"'{response_text[:500]}'")
                    return None

        except Exception as e:
            print(f"💥 EXCEPTION: {e}")
            return None

    async def debug_all_mirrors(self, tags="", limit=5):
        """Debug all API mirrors"""
        print(f"\n🔥 DEBUGGING ALL MIRRORS FOR TAGS: '{tags}'")

        for i, mirror in enumerate(self.api_mirrors, 1):
            print(f"\n--- MIRROR {i}/{len(self.api_mirrors)} ---")
            result = await self.debug_api_response(mirror, tags, limit)

            if result is not None and len(result) > 0:
                print(f"✅ {mirror['name']} SUCCESS - Found {len(result)} posts")
                return result
            else:
                print(f"❌ {mirror['name']} FAILED - No usable posts")

            # Small delay between mirrors
            await asyncio.sleep(2)

        print("\n💀 ALL MIRRORS FAILED")
        return []

    # ===== COMMANDS =====

    @commands.command(name='r34debug')
    async def debug_search(self, ctx, *, tags=""):
        """Debug Rule34 search with detailed logging"""
        if not self.is_nsfw(ctx.channel):
            return await ctx.send("❌ Use in NSFW channels only")

        embed = discord.Embed(title="🔧 Running Debug Search...", color=0xF39C12)
        embed.description = f"Debugging search for: `{tags or 'rating:explicit'}`"
        debug_message = await ctx.send(embed=embed)

        print(f"\n👤 DEBUG REQUEST from {ctx.author}: '{tags}'")

        async with ctx.typing():
            results = await self.debug_all_mirrors(tags or "rating:explicit", limit=10)

            if results:
                # Success - show post
                selected_post = random.choice(results)

                embed = discord.Embed(title="✅ Debug Search Successful", color=0x00FF00)
                embed.add_field(name="Posts Found", value=str(len(results)), inline=True)
                embed.add_field(name="Selected Post ID", value=str(selected_post.get('id', 'N/A')), inline=True)
                embed.add_field(name="Has File URL", value="✅ Yes" if selected_post.get('file_url') else "❌ No", inline=True)

                if selected_post.get('file_url'):
                    embed.set_image(url=selected_post['file_url'])

                embed.set_footer(text="Check console/logs for detailed debug info")

            else:
                # Failed - show debug info
                embed = discord.Embed(title="❌ Debug Search Failed", color=0xFF0000)
                embed.add_field(name="Result", value="No posts found from any mirror", inline=False)
                embed.add_field(name="Debug Info", 
                              value="• Check console output for detailed logs\n• See `rule34_debug.log` for full details\n• Try different tags or `!r34debugraw` for raw responses", 
                              inline=False)

            await debug_message.edit(embed=embed)

    @commands.command(name='r34debugraw')
    async def debug_raw(self, ctx, mirror_index: int = 1, *, tags=""):
        """Show raw API response for debugging"""
        if not self.is_nsfw(ctx.channel):
            return await ctx.send("❌ Use in NSFW channels only")

        if mirror_index < 1 or mirror_index > len(self.api_mirrors):
            return await ctx.send(f"❌ Mirror index must be 1-{len(self.api_mirrors)}")

        mirror = self.api_mirrors[mirror_index - 1]

        async with ctx.typing():
            print(f"\n🔍 RAW DEBUG for {mirror['name']}")

            # Get raw response
            params = mirror["params_template"].copy()
            if mirror["name"] == "E621":
                params.update({"limit": 5, "tags": tags})
            else:
                params.update({"tags": tags or "rating:explicit", "limit": 5})

            try:
                async with self.session.get(mirror["url"], params=params) as response:
                    response_text = await response.text()

                    embed = discord.Embed(title=f"🔍 Raw Response: {mirror['name']}", color=0x9B59B6)
                    embed.add_field(name="URL", value=mirror["url"], inline=False)
                    embed.add_field(name="Status Code", value=str(response.status), inline=True)
                    embed.add_field(name="Content Type", value=response.headers.get('content-type', 'unknown'), inline=True)
                    embed.add_field(name="Response Length", value=f"{len(response_text)} chars", inline=True)

                    # Show response preview
                    preview = response_text[:1000]
                    if len(response_text) > 1000:
                        preview += "\n... [truncated]"

                    embed.add_field(name="Response Preview", value=f"```json\n{preview}\n```", inline=False)

                    await ctx.send(embed=embed)

            except Exception as e:
                await ctx.send(f"❌ Error getting raw response: {e}")

    @commands.command(name='r34mirrors')
    async def show_mirrors(self, ctx):
        """Show available API mirrors"""
        embed = discord.Embed(title="🌐 Available API Mirrors", color=0x3498DB)

        for i, mirror in enumerate(self.api_mirrors, 1):
            embed.add_field(
                name=f"{i}. {mirror['name']}", 
                value=f"URL: {mirror['url']}\nUse with: `!r34debugraw {i}`", 
                inline=False
            )

        embed.set_footer(text="Use !r34debug to test all mirrors automatically")
        await ctx.send(embed=embed)

    @commands.command(name='r34debughelp')
    async def debug_help(self, ctx):
        """Show debug commands help"""
        embed = discord.Embed(title="🔧 Rule34 Debug Commands", color=0x1ABC9C)

        cmds = [
            "`!r34debug [tags]` - Full debug search with detailed logging",
            "`!r34debugraw <mirror_id> [tags]` - Show raw API response",
            "`!r34mirrors` - List all API mirrors",
            "`!r34debughelp` - Show this help"
        ]

        embed.add_field(name="Debug Commands", value="\n".join(cmds), inline=False)
        embed.add_field(name="📋 Notes", 
                       value="• All debug info printed to console\n• Logs saved to `rule34_debug.log`\n• Use in NSFW channels only", 
                       inline=False)

        await ctx.send(embed=embed)

    # ===== SLASH COMMANDS =====

    @app_commands.command(name="r34search", description="Search Rule34 APIs (NSFW channels only)")
    @app_commands.describe(tags="Tags to search for", mirror="Mirror index 1-4 (default: 1)")
    async def r34search_slash(self, interaction: discord.Interaction, tags: str = "", mirror: int = 1):
        if not getattr(interaction.channel, 'nsfw', False):
            await interaction.response.send_message("❌ NSFW channels only!", ephemeral=True)
            return

        if not 1 <= mirror <= len(self.api_mirrors):
            await interaction.response.send_message(f"❌ Mirror must be 1-{len(self.api_mirrors)}", ephemeral=True)
            return

        await interaction.response.defer()

        selected = self.api_mirrors[mirror - 1]
        results = await self.debug_api_response(selected, tags or "rating:explicit", limit=20)

        if not results:
            await interaction.followup.send(embed=discord.Embed(
                title="❌ No Results",
                description=f"No posts found for `{tags or 'rating:explicit'}` on **{selected['name']}**.\nCheck console logs for details.",
                color=0xFF0000))
            return

        post = random.choice(results)
        embed = discord.Embed(title=f"🔞 {tags or 'Random'}", color=0xFF69B4)
        url = post.get("file_url") or post.get("sample_url") or ""
        if url:
            embed.set_image(url=url)
        tag_preview = " ".join(f"`{t}`" for t in post.get("tags", "").split()[:8])
        embed.add_field(name="Tags", value=tag_preview or "None", inline=False)
        embed.add_field(name="Score", value=str(post.get("score", 0)), inline=True)
        embed.add_field(name="Source", value=selected["name"], inline=True)
        embed.set_footer(text=f"Post ID: {post.get('id', '?')} | {len(results)} results found")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="r34mirrors", description="List available Rule34 API mirrors")
    async def r34mirrors_slash(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🌐 Available API Mirrors", color=0x3498DB)
        for i, m in enumerate(self.api_mirrors, 1):
            embed.add_field(name=f"{i}. {m['name']}", value=m['url'], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Rule34DebugCog(bot))