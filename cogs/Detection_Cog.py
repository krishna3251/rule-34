import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import logging
import sys
from datetime import datetime, timedelta
import json
import re

# Setup logging for CF checker
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('cf_checker.log', encoding='utf-8')
    ])


class CloudflareCheckerCog(commands.Cog):
    """Dedicated Cloudflare and CAPTCHA detection cog"""

    def __init__(self, bot):
        self.bot = bot
        self.session = None
        self.logger = logging.getLogger('CFChecker')
        self.logger.setLevel(logging.DEBUG)

        # Test sites for CF detection
        self.test_sites = [{
            "name": "Rule34.xxx",
            "url": "https://rule34.xxx/index.php",
            "api": True
        }, {
            "name": "Rule34 API",
            "url": "https://api.rule34.xxx/index.php",
            "api": True
        }, {
            "name": "Gelbooru",
            "url": "https://gelbooru.com/index.php",
            "api": True
        }, {
            "name": "Danbooru",
            "url": "https://danbooru.donmai.us/posts.json",
            "api": True
        }, {
            "name": "E621",
            "url": "https://e621.net/posts.json",
            "api": True
        }, {
            "name": "Safebooru",
            "url": "https://safebooru.org/index.php",
            "api": True
        }, {
            "name": "Sankaku",
            "url": "https://capi-v2.sankakucomplex.com/posts",
            "api": True
        }, {
            "name": "Google",
            "url": "https://www.google.com",
            "api": False
        }, {
            "name": "Httpbin",
            "url": "https://httpbin.org/get",
            "api": True
        }]

        # CF/CAPTCHA detection patterns
        self.cf_patterns = {
            "cloudflare_basic":
            ["cloudflare", "cf-ray", "ray id", "__cf_bm", "cf_clearance"],
            "protection_active": [
                "checking your browser", "ddos protection", "security check",
                "please wait", "verifying you are human", "one more step"
            ],
            "challenge_page": [
                "challenge-platform", "challenge-running", "challenge-stage",
                "cf-challenge", "cf-spinner-redirecting"
            ],
            "captcha_detected": [
                "captcha", "hcaptcha", "recaptcha", "turnstile",
                "human verification", "prove you are human"
            ],
            "bot_detection": [
                "bot detected", "automated traffic", "suspicious activity",
                "blocked request", "access denied"
            ],
            "rate_limiting": [
                "rate limit", "too many requests", "slow down",
                "try again later", "429", "throttled"
            ]
        }

        # Status tracking
        self.site_status = {}
        self.last_check = None

        print("🛡️ CLOUDFLARE CHECKER INITIALIZING")
        self.logger.info("🛡️ Cloudflare Checker initialized")

    async def cog_load(self):
        """Initialize session with multiple user agents"""
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:89.0) Gecko/20100101 Firefox/89.0'
        ]

        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            headers={'User-Agent': self.user_agents[0]})

        print("🚀 CF Checker session created")
        self.logger.info("🚀 CF Checker session initialized")

    async def cog_unload(self):
        if self.session:
            await self.session.close()
            print("🔌 CF Checker session closed")
            self.logger.info("🔌 CF Checker session closed")

    def analyze_response(self, response_text, status_code, url):
        """Detailed CF/CAPTCHA analysis"""
        text_lower = response_text.lower()
        detected_issues = []
        severity = "✅ CLEAR"

        print(f"🔍 ANALYZING: {url} - Status: {status_code}")

        # Check each pattern category
        for category, patterns in self.cf_patterns.items():
            matches = [
                pattern for pattern in patterns if pattern in text_lower
            ]
            if matches:
                detected_issues.append({
                    "category": category,
                    "matches": matches,
                    "severity": self.get_severity(category)
                })

        # Determine overall status
        if any(issue["severity"] == "🚫 BLOCKED" for issue in detected_issues):
            severity = "🚫 BLOCKED"
        elif any(issue["severity"] == "⚠️ LIMITED"
                 for issue in detected_issues):
            severity = "⚠️ LIMITED"
        elif detected_issues:
            severity = "🔶 DETECTED"

        # Additional checks
        content_type = "Unknown"
        if "<!doctype html" in text_lower or "<html" in text_lower:
            content_type = "HTML Page"
        elif text_lower.strip().startswith(
                '{') or text_lower.strip().startswith('['):
            content_type = "JSON/API"
        elif text_lower.strip() == "":
            content_type = "Empty Response"

        result = {
            "url": url,
            "status_code": status_code,
            "severity": severity,
            "content_type": content_type,
            "response_length": len(response_text),
            "issues": detected_issues,
            "timestamp": datetime.now()
        }

        print(
            f"📊 RESULT: {severity} - {content_type} - {len(detected_issues)} issues"
        )
        return result

    def get_severity(self, category):
        """Get severity level for each category"""
        severity_map = {
            "cloudflare_basic": "🔶 DETECTED",
            "protection_active": "🚫 BLOCKED",
            "challenge_page": "🚫 BLOCKED",
            "captcha_detected": "🚫 BLOCKED",
            "bot_detection": "🚫 BLOCKED",
            "rate_limiting": "⚠️ LIMITED"
        }
        return severity_map.get(category, "🔶 DETECTED")

    async def test_site(self, site_info, user_agent=None):
        """Test a single site for CF/CAPTCHA"""
        site_name = site_info["name"]
        site_url = site_info["url"]
        is_api = site_info["api"]

        headers = {}
        if user_agent:
            headers['User-Agent'] = user_agent

        # Add API-specific parameters if it's an API endpoint
        params = {}
        if is_api and "rule34" in site_url.lower():
            params = {
                'page': 'dapi',
                's': 'post',
                'q': 'index',
                'json': '1',
                'limit': 1
            }
        elif is_api and "danbooru" in site_url.lower():
            params = {'limit': 1}
        elif is_api and "e621" in site_url.lower():
            params = {'limit': 1}

        try:
            print(f"🌐 TESTING: {site_name} - {site_url}")
            self.logger.info(f"🌐 Testing {site_name}: {site_url}")

            async with self.session.get(site_url,
                                        params=params,
                                        headers=headers) as response:
                response_text = await response.text()

                result = self.analyze_response(response_text, response.status,
                                               site_url)
                result["site_name"] = site_name
                result["is_api"] = is_api
                result["user_agent"] = user_agent or "Default"

                # Log detailed results
                self.logger.info(
                    f"📊 {site_name}: {result['severity']} - Status {response.status}"
                )
                if result["issues"]:
                    for issue in result["issues"]:
                        self.logger.warning(
                            f"⚠️ {site_name} - {issue['category']}: {issue['matches']}"
                        )

                return result

        except asyncio.TimeoutError:
            print(f"⏱️ {site_name} TIMEOUT")
            self.logger.error(f"⏱️ {site_name} - Timeout after 20s")
            return {
                "site_name":
                site_name,
                "url":
                site_url,
                "status_code":
                "TIMEOUT",
                "severity":
                "🔴 TIMEOUT",
                "issues": [{
                    "category": "timeout",
                    "matches": ["connection timeout"],
                    "severity": "🔴 TIMEOUT"
                }],
                "timestamp":
                datetime.now()
            }
        except Exception as e:
            print(f"💥 {site_name} ERROR: {e}")
            self.logger.error(f"💥 {site_name} - Error: {e}")
            return {
                "site_name":
                site_name,
                "url":
                site_url,
                "status_code":
                "ERROR",
                "severity":
                "🔴 ERROR",
                "issues": [{
                    "category": "connection_error",
                    "matches": [str(e)],
                    "severity": "🔴 ERROR"
                }],
                "timestamp":
                datetime.now()
            }

    async def run_full_check(self):
        """Run comprehensive CF check on all sites"""
        print("🔥 STARTING COMPREHENSIVE CF/CAPTCHA CHECK")
        self.logger.info("🔥 Starting comprehensive CF/CAPTCHA check")

        results = []

        for site in self.test_sites:
            result = await self.test_site(site)
            results.append(result)
            self.site_status[site["name"]] = result

            # Small delay between requests
            await asyncio.sleep(1)

        self.last_check = datetime.now()
        print(f"✅ CF CHECK COMPLETE - {len(results)} sites tested")
        self.logger.info(f"✅ CF check complete - {len(results)} sites tested")

        return results

    # ===== COMMANDS =====

    @commands.command(name='cfcheck')
    async def cf_check(self, ctx):
        """Run Cloudflare/CAPTCHA detection check"""
        embed = discord.Embed(title="🛡️ Running CF/CAPTCHA Check...",
                              color=0x1ABC9C)
        embed.description = "Testing all sites for blocking issues..."
        message = await ctx.send(embed=embed)

        async with ctx.typing():
            results = await self.run_full_check()

            # Create results embed
            embed = discord.Embed(
                title="🛡️ Cloudflare & CAPTCHA Detection Results",
                color=0x3498DB)

            blocked_sites = []
            limited_sites = []
            clear_sites = []
            error_sites = []

            for result in results:
                site_name = result["site_name"]
                severity = result["severity"]
                status = result.get("status_code", "Unknown")

                if "BLOCKED" in severity:
                    blocked_sites.append(f"🚫 {site_name} - {status}")
                elif "LIMITED" in severity:
                    limited_sites.append(f"⚠️ {site_name} - {status}")
                elif "TIMEOUT" in severity or "ERROR" in severity:
                    error_sites.append(f"🔴 {site_name} - {status}")
                else:
                    clear_sites.append(f"✅ {site_name} - {status}")

            # Add fields to embed
            if blocked_sites:
                embed.add_field(name="🚫 Blocked Sites",
                                value="\n".join(blocked_sites),
                                inline=False)
            if limited_sites:
                embed.add_field(name="⚠️ Limited Sites",
                                value="\n".join(limited_sites),
                                inline=False)
            if error_sites:
                embed.add_field(name="🔴 Connection Issues",
                                value="\n".join(error_sites),
                                inline=False)
            if clear_sites:
                embed.add_field(name="✅ Clear Sites",
                                value="\n".join(clear_sites),
                                inline=False)

            # Summary
            total = len(results)
            blocked_count = len(blocked_sites)
            clear_count = len(clear_sites)

            embed.add_field(
                name="📊 Summary",
                value=
                f"Total: {total} | Clear: {clear_count} | Blocked: {blocked_count}",
                inline=False)

            embed.set_footer(
                text=f"Check completed at {datetime.now().strftime('%H:%M:%S')}"
            )

            await message.edit(embed=embed)

    @commands.command(name='cfdetail')
    async def cf_detail(self, ctx, *, site_name=""):
        """Get detailed CF analysis for a specific site"""
        if not site_name:
            site_names = [site["name"] for site in self.test_sites]
            embed = discord.Embed(title="🔍 Available Sites", color=0x9B59B6)
            embed.description = "Use `!cfdetail <site_name>` with one of these:\n\n" + "\n".join(
                [f"• {name}" for name in site_names])
            return await ctx.send(embed=embed)

        # Find matching site
        target_site = None
        for site in self.test_sites:
            if site_name.lower() in site["name"].lower():
                target_site = site
                break

        if not target_site:
            return await ctx.send(embed=discord.Embed(
                title="❌ Site Not Found",
                description=f"'{site_name}' not found in test sites.",
                color=0xE74C3C))

        async with ctx.typing():
            result = await self.test_site(target_site)

            embed = discord.Embed(
                title=f"🔍 Detailed Analysis: {result['site_name']}",
                color=0x3498DB)
            embed.add_field(name="🌐 URL", value=result["url"], inline=False)
            embed.add_field(
                name="📊 Status",
                value=
                f"{result.get('status_code', 'Unknown')} - {result['severity']}",
                inline=True)
            embed.add_field(name="📄 Content Type",
                            value=result.get("content_type", "Unknown"),
                            inline=True)
            embed.add_field(name="📏 Response Size",
                            value=f"{result.get('response_length', 0)} chars",
                            inline=True)

            if result.get("issues"):
                issues_text = []
                for issue in result["issues"]:
                    category = issue["category"].replace("_", " ").title()
                    matches = ", ".join(
                        issue["matches"][:3])  # Show first 3 matches
                    issues_text.append(f"**{category}**: {matches}")

                embed.add_field(name="⚠️ Detected Issues",
                                value="\n".join(issues_text),
                                inline=False)
            else:
                embed.add_field(name="✅ Status",
                                value="No blocking issues detected",
                                inline=False)

            embed.set_footer(
                text=f"Tested at {datetime.now().strftime('%H:%M:%S')}")
            await ctx.send(embed=embed)

    @commands.command(name='cfstatus')
    async def cf_status(self, ctx):
        """Show current CF status from last check"""
        if not self.site_status or not self.last_check:
            return await ctx.send(embed=discord.Embed(
                title="❓ No Data",
                description="Run `!cfcheck` first to collect data.",
                color=0x95A5A6))

        embed = discord.Embed(title="📊 Current CF Status", color=0x2ECC71)

        status_groups = {
            "✅ Clear": [],
            "⚠️ Limited": [],
            "🚫 Blocked": [],
            "🔴 Issues": []
        }

        for site_name, result in self.site_status.items():
            severity = result["severity"]
            if "CLEAR" in severity:
                status_groups["✅ Clear"].append(site_name)
            elif "LIMITED" in severity:
                status_groups["⚠️ Limited"].append(site_name)
            elif "BLOCKED" in severity:
                status_groups["🚫 Blocked"].append(site_name)
            else:
                status_groups["🔴 Issues"].append(site_name)

        for status, sites in status_groups.items():
            if sites:
                embed.add_field(name=status,
                                value="\n".join(sites),
                                inline=True)

        time_ago = datetime.now() - self.last_check
        embed.set_footer(
            text=f"Last check: {int(time_ago.total_seconds() / 60)} minutes ago"
        )

        await ctx.send(embed=embed)

    @commands.command(name='cftest')
    async def cf_test_single(self, ctx, *, url=""):
        """Test a custom URL for CF/CAPTCHA"""
        if not url:
            return await ctx.send(embed=discord.Embed(
                title="❓ Usage",
                description=
                "Usage: `!cftest <url>`\nExample: `!cftest https://example.com`",
                color=0x95A5A6))

        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        custom_site = {"name": "Custom Test", "url": url, "api": False}

        async with ctx.typing():
            result = await self.test_site(custom_site)

            embed = discord.Embed(title="🧪 Custom URL Test", color=0xF39C12)
            embed.add_field(name="🌐 URL", value=result["url"], inline=False)
            embed.add_field(name="📊 Result",
                            value=result["severity"],
                            inline=True)
            embed.add_field(name="🔢 Status Code",
                            value=str(result.get("status_code", "Unknown")),
                            inline=True)
            embed.add_field(name="📄 Content Type",
                            value=result.get("content_type", "Unknown"),
                            inline=True)

            if result.get("issues"):
                issues_summary = []
                for issue in result["issues"]:
                    issues_summary.append(issue["category"].replace(
                        "_", " ").title())
                embed.add_field(name="⚠️ Issues Found",
                                value=", ".join(set(issues_summary)),
                                inline=False)

            await ctx.send(embed=embed)

    @commands.command(name='cfhelp')
    async def cf_help(self, ctx):
        """Show CF Checker help"""
        embed = discord.Embed(title="🛡️ Cloudflare & CAPTCHA Checker",
                              color=0x1ABC9C)

        cmds = [
            "`!cfcheck` - Run full CF/CAPTCHA check on all sites",
            "`!cfstatus` - Show current blocking status summary",
            "`!cfdetail <site>` - Detailed analysis of specific site",
            "`!cftest <url>` - Test custom URL for blocking",
            "`!cfhelp` - Show this help message"
        ]

        embed.add_field(name="Commands", value="\n".join(cmds), inline=False)

        detection_types = [
            "🛡️ **Cloudflare Detection** - CF-Ray, challenge pages",
            "🤖 **Bot Detection** - Automated traffic blocking",
            "🧩 **CAPTCHA Detection** - reCAPTCHA, hCaptcha, Turnstile",
            "⏰ **Rate Limiting** - Too many requests errors",
            "🚫 **Access Denied** - 403/401 forbidden responses"
        ]

        embed.add_field(name="Detection Types",
                        value="\n".join(detection_types),
                        inline=False)
        embed.add_field(
            name="📋 Note",
            value="Results are logged to console and `cf_checker.log`",
            inline=False)

        await ctx.send(embed=embed)


    # ===== SLASH COMMANDS =====

    @app_commands.command(name="cfcheck", description="Run Cloudflare/CAPTCHA detection on all API sites")
    async def cfcheck_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        results = await self.run_full_check()

        embed = discord.Embed(title="🛡️ CF/CAPTCHA Detection Results", color=0x3498DB)
        blocked, limited, clear, errors = [], [], [], []

        for r in results:
            name = r["site_name"]
            sev = r["severity"]
            status = r.get("status_code", "?")
            if "BLOCKED" in sev:
                blocked.append(f"🚫 {name} ({status})")
            elif "LIMITED" in sev:
                limited.append(f"⚠️ {name} ({status})")
            elif "TIMEOUT" in sev or "ERROR" in sev:
                errors.append(f"🔴 {name} ({status})")
            else:
                clear.append(f"✅ {name} ({status})")

        if blocked:
            embed.add_field(name="🚫 Blocked", value="\n".join(blocked), inline=False)
        if limited:
            embed.add_field(name="⚠️ Limited", value="\n".join(limited), inline=False)
        if errors:
            embed.add_field(name="🔴 Errors", value="\n".join(errors), inline=False)
        if clear:
            embed.add_field(name="✅ Clear", value="\n".join(clear), inline=False)

        embed.set_footer(text=f"Tested {len(results)} sites | {len(clear)} clear, {len(blocked)} blocked")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="cfstatus", description="Show current CF status from last check")
    async def cfstatus_slash(self, interaction: discord.Interaction):
        if not self.site_status:
            await interaction.response.send_message("❌ No data yet. Run `/cfcheck` first.", ephemeral=True)
            return

        embed = discord.Embed(title="📊 Current CF Status", color=0x2ECC71)
        groups = {"✅ Clear": [], "⚠️ Limited": [], "🚫 Blocked": [], "🔴 Issues": []}
        for site_name, result in self.site_status.items():
            sev = result["severity"]
            if "CLEAR" in sev:
                groups["✅ Clear"].append(site_name)
            elif "LIMITED" in sev:
                groups["⚠️ Limited"].append(site_name)
            elif "BLOCKED" in sev:
                groups["🚫 Blocked"].append(site_name)
            else:
                groups["🔴 Issues"].append(site_name)

        for status, sites in groups.items():
            if sites:
                embed.add_field(name=status, value="\n".join(sites), inline=True)

        if self.last_check:
            mins_ago = int((datetime.now() - self.last_check).total_seconds() / 60)
            embed.set_footer(text=f"Last check: {mins_ago} minutes ago")

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(CloudflareCheckerCog(bot))
