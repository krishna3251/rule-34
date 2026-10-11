import os
import secrets
import discord
from discord.ext import commands
from discord import app_commands
import logging
import asyncio
import hashlib
import time
import random
import string
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class VerificationCog(commands.Cog):
    """Enhanced age verification and user management with advanced security"""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.pending_verifications = {}
        self.verification_attempts = {}
        self.rate_limits = {}
        target_guild_id = os.getenv("VERIFICATION_TARGET_GUILD_ID", "0")
        self.target_server_id = int(target_guild_id or 0) or None

        # Security configurations
        self.max_attempts_per_hour = 3
        self.verification_timeout = 300  # 5 minutes
        self.cooldown_period = 3600  # 1 hour
        self.min_account_age_days = 7  # Minimum account age requirement

    def generate_verification_token(self, user_id):
        """Generate a unique verification token for enhanced security"""
        return secrets.token_urlsafe(18)

    def is_rate_limited(self, user_id, guild_id=None):
        """Check if user is rate limited"""
        key = (guild_id, user_id)
        current_time = time.time()
        if key not in self.rate_limits:
            self.rate_limits[key] = []

        # Clean old attempts (older than 1 hour)
        self.rate_limits[key] = [
            attempt_time for attempt_time in self.rate_limits[key]
            if current_time - attempt_time < self.cooldown_period
        ]

        return len(self.rate_limits[key]) >= self.max_attempts_per_hour

    def add_rate_limit_attempt(self, user_id, guild_id=None):
        """Add a verification attempt to rate limiting"""
        key = (guild_id, user_id)
        if key not in self.rate_limits:
            self.rate_limits[key] = []
        self.rate_limits[key].append(time.time())

    async def check_account_security(self, user):
        """Enhanced security checks for user account"""
        current_time = datetime.now(timezone.utc)
        account_age = current_time - user.created_at

        security_issues = []

        # Check account age
        if account_age.days < self.min_account_age_days:
            security_issues.append(f"Account too new (created {account_age.days} days ago)")

        # Check if user has avatar (basic legitimacy check)
        if not user.avatar:
            security_issues.append("No profile picture set")

        # Check username for suspicious patterns
        suspicious_patterns = ['bot', '1234', 'temp', 'test', 'fake']
        username_lower = user.name.lower()
        if any(pattern in username_lower for pattern in suspicious_patterns):
            security_issues.append("Suspicious username pattern detected")

        return security_issues

    async def log_verification_attempt(self, user, success=False, reason="", guild_id=None):
        """Log verification attempts for audit trail"""
        try:
            self.db.log_verification_attempt(
                user_id=user.id,
                username=str(user),
                success=success,
                reason=reason,
                timestamp=datetime.now(timezone.utc).isoformat(),
                ip_hash=None,
                guild_id=guild_id,
            )
        except Exception as e:
            logger.error(f"Failed to log verification attempt: {e}")

    @commands.command(name='verify')
    @commands.guild_only()
    @commands.cooldown(1, 30, commands.BucketType.member)  # 30 second cooldown per guild member
    async def verify_user(self, ctx):
        """Enhanced verification command with security measures"""
        try:
            # Rate limiting check
            if self.is_rate_limited(ctx.author.id, ctx.guild.id):
                await self.log_verification_attempt(ctx.author, False, "Rate limited", ctx.guild.id)
                embed = discord.Embed(
                    title="🚫 Rate Limited",
                    description="You have exceeded the maximum verification attempts. Please try again later.",
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="⏰ Cooldown",
                    value="Please wait 1 hour before attempting verification again.",
                    inline=False
                )
                await ctx.send(embed=embed, delete_after=15)
                await self.safe_delete_message(ctx.message, delay=15)
                return

            verification_key = (ctx.guild.id, ctx.author.id)
            existing = self.pending_verifications.get(verification_key)
            if existing and time.time() - existing['timestamp'] <= self.verification_timeout:
                await ctx.send(
                    "⏳ You already have an active verification session. Please finish it first.",
                    delete_after=10,
                )
                return

            # Check if already verified
            if self.db.is_user_verified(ctx.author.id, ctx.guild.id):
                embed = discord.Embed(
                    title="✅ Already Verified",
                    description="You are already verified on this server.",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed, delete_after=10)
                await self.safe_delete_message(ctx.message, delay=10)
                return

            # Security checks
            security_issues = await self.check_account_security(ctx.author)
            if security_issues:
                await self.log_verification_attempt(ctx.author, False, f"Security issues: {', '.join(security_issues)}", ctx.guild.id)
                embed = discord.Embed(
                    title="🔒 Security Check Failed",
                    description="Your account does not meet the security requirements for verification.",
                    color=discord.Color.red()
                )
                embed.add_field(
                    name="⚠️ Issues Detected",
                    value="\n".join([f"• {issue}" for issue in security_issues]),
                    inline=False
                )
                embed.add_field(
                    name="📞 Support",
                    value="If you believe this is an error, please contact server moderators.",
                    inline=False
                )
                await ctx.send(embed=embed, delete_after=30)
                await self.safe_delete_message(ctx.message, delay=30)
                return

            # Generate verification token
            verification_token = self.generate_verification_token(ctx.author.id)

            # Create verification embed
            embed = discord.Embed(
                title="🔞 Enhanced Age Verification Required",
                description="This server contains adult content. Complete verification to continue.",
                color=discord.Color.orange()
            )
            embed.add_field(
                name="📋 Verification Requirements",
                value="• You must be 18 years of age or older\n"
                      "• You understand this server contains adult content\n"
                      "• You agree to follow all server rules and Discord ToS\n"
                      "• Your account meets security requirements",
                inline=False
            )
            embed.add_field(
                name="⏱️ Time Limit",
                value="You have 5 minutes to complete verification.",
                inline=False
            )
            embed.set_footer(text="React with ✅ to verify or ❌ to cancel")

            # Store verification data
            self.pending_verifications[verification_key] = {
                'token': verification_token,
                'timestamp': time.time(),
                'guild_id': ctx.guild.id,
                'attempts': self.verification_attempts.get(verification_key, 0) + 1
            }

            try:
                # Try to send DM first
                dm_message = await ctx.author.send(embed=embed)
                self.pending_verifications[verification_key]['message_id'] = dm_message.id
                await dm_message.add_reaction("✅")
                await dm_message.add_reaction("❌")

                await ctx.send("📨 Verification instructions sent to your DMs!", delete_after=10)
                await self.safe_delete_message(ctx.message, delay=10)

                # Set up timeout
                await self.setup_verification_timeout(ctx.author, ctx.guild.id)

            except discord.Forbidden:
                # Fallback to channel message if DMs are disabled
                embed.add_field(
                    name="🔒 DMs Required",
                    value="Please enable DMs and run the command again for secure verification.",
                    inline=False
                )
                message = await ctx.send(embed=embed, delete_after=60)
                self.pending_verifications[verification_key]['message_id'] = message.id
                await message.add_reaction("✅")
                await message.add_reaction("❌")
                await self.safe_delete_message(ctx.message, delay=60)

            # Add to rate limiting
            self.add_rate_limit_attempt(ctx.author.id, ctx.guild.id)

        except Exception as e:
            logger.error(f"Error in verify_user command: {e}")
            await self.handle_verification_error(ctx, "An unexpected error occurred during verification.")

    async def setup_verification_timeout(self, user, guild_id=None):
        """Set up automatic timeout for verification"""
        data = self.pending_verifications.get((guild_id, user.id))
        await asyncio.sleep(self.verification_timeout)
        if data is not None and self.pending_verifications.get((guild_id, user.id)) is data:
            await self.timeout_verification(user, guild_id)

    async def timeout_verification(self, user, guild_id=None):
        """Handle verification timeout"""
        key = (guild_id, user.id)
        if key in self.pending_verifications:
            del self.pending_verifications[key]
            await self.log_verification_attempt(user, False, "Verification timeout", guild_id)

            try:
                timeout_embed = discord.Embed(
                    title="⏰ Verification Timeout",
                    description="Your verification session has expired.",
                    color=discord.Color.orange()
                )
                timeout_embed.add_field(
                    name="🔄 Next Steps",
                    value="Please run the `!verify` command again to start a new verification session.",
                    inline=False
                )
                await user.send(embed=timeout_embed)
            except discord.Forbidden:
                logger.warning(f"Could not send timeout message to {user}")

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        """Enhanced reaction handling with security validation"""
        if user.bot:
            return

        try:
            # Check if this is a verification reaction
            matching = [item for key, item in self.pending_verifications.items()
                        if key[1] == user.id and item.get('message_id') == reaction.message.id]
            if not matching:
                return
            verification_data = matching[0]

            # Validate reaction is on correct message
            if not reaction.message.embeds:
                return

            expected_message_id = verification_data.get('message_id')
            if expected_message_id and reaction.message.id != expected_message_id:
                return

            embed = reaction.message.embeds[0]
            if "Enhanced Age Verification Required" not in embed.title:
                return

            # Handle verification response
            if str(reaction.emoji) == "✅":
                await self.complete_verification(user, reaction.message, verification_data)
            elif str(reaction.emoji) == "❌":
                await self.cancel_verification(user, verification_data)

        except Exception as e:
            logger.error(f"Error in reaction handling: {e}")
            await self.handle_verification_error(None, "Error processing verification reaction.", user)

    async def complete_verification(self, user, message, verification_data):
        """Complete the verification process with enhanced security"""
        try:
            # Validate timing
            if time.time() - verification_data['timestamp'] > self.verification_timeout:
                await self.timeout_verification(user, verification_data.get('guild_id'))
                return

            # Add to database
            guild_id = verification_data.get('guild_id')
            if not guild_id:
                return
            self.db.verify_user(user.id, guild_id)
            await self.log_verification_attempt(user, True, "Successfully verified", guild_id)

            # Add to target server
            await self.add_user_to_target_server(user)

            # Add roles only in the guild where this verification began.
            await self.assign_verification_roles(user, verification_data.get('guild_id'))

            # Clean up
            self.pending_verifications.pop((guild_id, user.id), None)

            # Send success message
            success_embed = discord.Embed(
                title="✅ Verification Complete",
                description="Welcome! You have been successfully verified and added to the server.",
                color=discord.Color.green()
            )
            success_embed.add_field(
                name="🎉 Access Granted",
                value="• You now have access to all server content\n"
                      "• Verification roles have been assigned\n"
                      "• You have been added to the main server",
                inline=False
            )
            success_embed.add_field(
                name="📋 Next Steps",
                value="Please read the server rules and introduce yourself!",
                inline=False
            )

            try:
                await user.send(embed=success_embed)
                await self.safe_delete_message(message)
            except discord.Forbidden:
                logger.warning(f"Could not send success message to {user}")

        except Exception as e:
            logger.error(f"Error completing verification for {user}: {e}")
            await self.handle_verification_error(None, "Error completing verification.", user)

    async def cancel_verification(self, user, verification_data):
        """Handle verification cancellation"""
        try:
            self.pending_verifications.pop((verification_data.get('guild_id'), user.id), None)

            await self.log_verification_attempt(
                user, False, "User cancelled verification", verification_data.get('guild_id')
            )

            cancel_embed = discord.Embed(
                title="❌ Verification Cancelled",
                description="You have cancelled the verification process.",
                color=discord.Color.red()
            )
            cancel_embed.add_field(
                name="🔄 Try Again",
                value="You can run `!verify` again when you're ready to complete verification.",
                inline=False
            )

            try:
                await user.send(embed=cancel_embed)
            except discord.Forbidden:
                logger.warning(f"Could not send cancellation message to {user}")

        except Exception as e:
            logger.error(f"Error cancelling verification for {user}: {e}")

    async def add_user_to_target_server(self, user):
        """Add verified user to the target server"""
        try:
            if not self.target_server_id:
                logger.info("Verification target guild is not configured; skipping invitation")
                return

            target_guild = self.bot.get_guild(self.target_server_id)
            if not target_guild:
                logger.error(f"Target server {self.target_server_id} not found")
                return

            # Check if user is already in the server
            member = target_guild.get_member(user.id)
            if member:
                logger.info(f"User {user} already in target server")
                return

            # Create invite to the server
            try:
                # Try to find a suitable channel for invite
                invite_channel = None
                for channel in target_guild.text_channels:
                    if channel.permissions_for(target_guild.me).create_instant_invite:
                        invite_channel = channel
                        break

                if invite_channel:
                    invite = await invite_channel.create_invite(
                        max_uses=1,
                        max_age=3600,  # 1 hour
                        unique=True,
                        reason=f"Verification invite for {user}"
                    )

                    invite_embed = discord.Embed(
                        title="🎊 Server Invitation",
                        description="You've been invited to join our main server!",
                        color=discord.Color.blue()
                    )
                    invite_embed.add_field(
                        name="🔗 Invitation Link",
                        value=f"[Click here to join]({invite.url})",
                        inline=False
                    )
                    invite_embed.add_field(
                        name="⏰ Expires",
                        value="This invitation expires in 1 hour.",
                        inline=False
                    )

                    await user.send(embed=invite_embed)
                    logger.info(f"Sent server invite to {user}")

            except discord.Forbidden:
                logger.error("Cannot create invite in target server")
            except Exception as e:
                logger.error(f"Error creating server invite: {e}")

        except Exception as e:
            logger.error(f"Error adding user to target server: {e}")

    async def assign_verification_roles(self, user, guild_id=None):
        """Assign verification roles only in the originating guild."""
        if not guild_id:
            return

        try:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                logger.warning(f"Verification guild {guild_id} is unavailable")
                return

            member = guild.get_member(user.id)
            if not member:
                return

            try:
                settings = self.db.get_server_settings(guild.id) or {}
                verification_role_id = settings.get('verification_role_id')

                if verification_role_id:
                    role = guild.get_role(verification_role_id)
                    if role and role not in member.roles:
                        await member.add_roles(role, reason="Age verification completed")
                        logger.info(
                            f"Added verification role to {member} in {guild.name}"
                        )

                verified_role = discord.utils.get(guild.roles, name="Verified")
                if verified_role and verified_role not in member.roles:
                    await member.add_roles(
                        verified_role, reason="Age verification completed"
                    )
                    logger.info(
                        f"Added 'Verified' role to {member} in {guild.name}"
                    )

            except discord.Forbidden:
                logger.warning(f"Cannot add verification role in {guild.name}")
            except Exception as e:
                logger.error(
                    f"Error assigning verification role in {guild.name}: {e}"
                )

        except Exception as e:
            logger.error(f"Error in assign_verification_roles: {e}")

    async def handle_verification_error(self, ctx, message, user=None):
        """Centralized error handling for verification process"""
        try:
            embed = discord.Embed(
                title="⚠️ Verification Error",
                description=message,
                color=discord.Color.red()
            )
            embed.add_field(
                name="🛠️ Support",
                value="Please contact server administrators if this issue persists.",
                inline=False
            )

            if ctx:
                await ctx.send(embed=embed, delete_after=30)
            elif user:
                try:
                    await user.send(embed=embed)
                except discord.Forbidden:
                    pass

        except Exception as e:
            logger.error(f"Error in error handler: {e}")

    async def safe_delete_message(self, message, delay=0):
        """Safely delete a message with error handling"""
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            pass  # Message already deleted or no permission
        except Exception as e:
            logger.warning(f"Error deleting message: {e}")

    @commands.command(name='verification_stats')
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def verification_stats(self, ctx):
        """Display verification statistics (Admin only)"""
        try:
            stats = self.db.get_verification_stats(ctx.guild.id)

            embed = discord.Embed(
                title="📊 Verification Statistics",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="✅ Total Verified Users",
                value=stats.get('total_verified', 0),
                inline=True
            )
            embed.add_field(
                name="🔄 Pending Verifications",
                value=sum(key[0] == ctx.guild.id for key in self.pending_verifications),
                inline=True
            )
            embed.add_field(
                name="🚫 Failed Attempts (24h)",
                value=stats.get('failed_24h', 0),
                inline=True
            )

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in verification_stats: {e}")
            await ctx.send("Error retrieving verification statistics.")

    @commands.command(name='force_verify')
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def force_verify(self, ctx, member: discord.Member):
        """Force verify a user (Admin only)"""
        try:
            self.db.verify_user(member.id, ctx.guild.id)
            await self.assign_verification_roles(member, ctx.guild.id)
            await self.add_user_to_target_server(member)

            embed = discord.Embed(
                title="✅ Force Verification Complete",
                description=f"{member.mention} has been manually verified.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)

            await self.log_verification_attempt(member, True, f"Force verified by {ctx.author}", ctx.guild.id)

        except Exception as e:
            logger.error(f"Error in force_verify: {e}")
            await ctx.send("Error force verifying user.")

    @commands.command(name="verification_status", aliases=["verifystatus", "vstatus"])
    @commands.guild_only()
    async def verification_status_prefix(self, ctx: commands.Context) -> None:
        """Check your verification status."""
        verified = self.db.is_user_verified(ctx.author.id, ctx.guild.id)
        embed = discord.Embed(
            title="🔍 Verification Status",
            description=(
                "✅ Verified"
                if verified
                else "❌ Not verified — use ~verify to start verification.",
            ),
            color=discord.Color.green() if verified else discord.Color.red(),
        )
        await ctx.send(embed=embed)

    # ===== SLASH COMMANDS =====

    @app_commands.command(name="verification_stats", description="Show verification statistics")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def verification_stats_slash(self, interaction: discord.Interaction):
        try:
            stats = self.db.get_verification_stats(interaction.guild_id)
            embed = discord.Embed(title="📊 Verification Statistics", color=discord.Color.blue())
            embed.add_field(name="✅ Total Verified Users", value=str(stats.get("total_verified", 0)), inline=True)
            embed.add_field(name="🔄 Pending Verifications", value=str(sum(key[0] == interaction.guild_id for key in self.pending_verifications)), inline=True)
            embed.add_field(name="🚫 Failed Attempts (24h)", value=str(stats.get("failed_24h", 0)), inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as exc:
            logger.error("Error in verification_stats slash: %s", exc)
            await interaction.response.send_message("❌ Error retrieving verification statistics.", ephemeral=True)
    @app_commands.command(name="verify", description="Verify your age to access this server")
    async def verify_slash(self, interaction: discord.Interaction):
        """Slash version of age verification"""
        try:
            if interaction.guild_id is None:
                await interaction.response.send_message(
                    "❌ Verification can only be started inside a server.", ephemeral=True
                )
                return

            if self.is_rate_limited(interaction.user.id, interaction.guild_id):
                await self.log_verification_attempt(interaction.user, False, "Rate limited", interaction.guild_id)
                await interaction.response.send_message(
                    "🚫 You have exceeded the maximum verification attempts. Please wait 1 hour.",
                    ephemeral=True)
                return

            if self.db.is_user_verified(interaction.user.id, interaction.guild_id):
                await interaction.response.send_message(
                    "✅ You are already verified!", ephemeral=True)
                return

            security_issues = await self.check_account_security(interaction.user)
            if security_issues:
                await self.log_verification_attempt(
                    interaction.user, False, f"Security issues: {', '.join(security_issues)}", interaction.guild_id
                )
                embed = discord.Embed(
                    title="🔒 Security Check Failed",
                    description="Your account does not meet the security requirements.",
                    color=discord.Color.red())
                embed.add_field(name="Issues", value="\n".join(f"• {i}" for i in security_issues))
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            verification_key = (interaction.guild_id, interaction.user.id)
            existing = self.pending_verifications.get(verification_key)
            if existing and time.time() - existing['timestamp'] <= self.verification_timeout:
                await interaction.response.send_message(
                    "⏳ You already have an active verification session. Please finish it first.",
                    ephemeral=True,
                )
                return
            token = self.generate_verification_token(interaction.user.id)
            self.pending_verifications[verification_key] = {
                'token': token,
                'timestamp': time.time(),
                'guild_id': interaction.guild_id,
                'attempts': self.verification_attempts.get(verification_key, 0) + 1
            }
            self.add_rate_limit_attempt(interaction.user.id, interaction.guild_id)

            embed = discord.Embed(
                title="🔞 Age Verification",
                description="Click **Confirm** below to confirm you are 18+ and agree to server rules.",
                color=discord.Color.orange())
            view = VerifyView(self, interaction.user, interaction.guild_id, token)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in verify slash: {e}")
            await interaction.response.send_message("❌ An error occurred.", ephemeral=True)

    @app_commands.command(name="verification_status", description="Check your verification status")
    @app_commands.guild_only()
    async def verification_status_slash(self, interaction: discord.Interaction):
        verified = self.db.is_user_verified(interaction.user.id, interaction.guild_id)
        embed = discord.Embed(
            title="🔍 Verification Status",
            description="✅ Verified" if verified else "❌ Not verified — use `/verify` to get verified.",
            color=discord.Color.green() if verified else discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="force_verify", description="Force verify a user (Admin only)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def force_verify_slash(self, interaction: discord.Interaction, member: discord.Member):
        try:
            self.db.verify_user(member.id, interaction.guild_id)
            await self.assign_verification_roles(member, interaction.guild_id)
            await self.add_user_to_target_server(member)
            await interaction.response.send_message(
                f"✅ {member.mention} has been force-verified.", ephemeral=True)
            await self.log_verification_attempt(member, True, f"Force verified by {interaction.user}", interaction.guild_id)
        except Exception as e:
            logger.error(f"Error in force_verify_slash: {e}")
            await interaction.response.send_message("❌ Error force verifying user.", ephemeral=True)


class VerifyView(discord.ui.View):
    """Inline button view for slash-based verification"""

    def __init__(self, cog: VerificationCog, user: discord.User, guild_id: int, token: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.user = user
        self.guild_id = guild_id
        self.token = token

    @discord.ui.button(label="✅ I am 18+ — Verify Me", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ This is not your verification.", ephemeral=True)
            return

        data = self.cog.pending_verifications.get((self.guild_id, self.user.id))
        if (interaction.guild_id != self.guild_id or not data
                or data.get('token') != self.token
                or time.time() - data['timestamp'] > self.cog.verification_timeout):
            await interaction.response.send_message("⏰ Session expired. Please run `/verify` again.", ephemeral=True)
            return

        guild_id = data.get('guild_id')
        self.cog.db.verify_user(self.user.id, guild_id)
        await self.cog.log_verification_attempt(self.user, True, "Slash verified", guild_id)
        await self.cog.assign_verification_roles(self.user, data.get('guild_id'))
        await self.cog.add_user_to_target_server(self.user)
        self.cog.pending_verifications.pop((guild_id, self.user.id), None)

        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(title="✅ Verified!", description="Welcome! You now have full access.", color=discord.Color.green()),
            view=None)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌", ephemeral=True)
            return
        key = (self.guild_id, self.user.id)
        data = self.cog.pending_verifications.get(key)
        if interaction.guild_id != self.guild_id or not data or data.get('token') != self.token:
            await interaction.response.send_message("⏰ Session expired.", ephemeral=True)
            return
        self.cog.pending_verifications.pop(key, None)
        await self.cog.log_verification_attempt(self.user, False, "User cancelled verification", self.guild_id)
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(title="❌ Cancelled", color=discord.Color.red()), view=None)


async def setup(bot):
    """Setup function to add cog to bot"""
    await bot.add_cog(VerificationCog(bot))
