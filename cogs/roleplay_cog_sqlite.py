import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import google.generativeai as genai
import asyncio
import os
from datetime import datetime, timedelta
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = "data/roleplay.db"


def db_connect():
    """Create database connection with proper error handling"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.error(f"Database connection error: {e}")
        raise


def init_db():
    """Initialize database with proper error handling"""
    try:
        # Create data directory if it doesn't exist
        os.makedirs("data", exist_ok=True)

        conn = db_connect()
        cur = conn.cursor()

        # Create tables with proper constraints
        cur.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                name TEXT PRIMARY KEY,
                prompt TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                user_id INTEGER PRIMARY KEY,
                character TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                last_active TIMESTAMP NOT NULL,
                FOREIGN KEY (character) REFERENCES characters(name)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")

    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")
        raise


class RoleplaySQL(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        init_db()
        self.idle_timeout = timedelta(minutes=5)
        self.idle_task = None
        self.model = None

        # Initialize Gemini API
        self._init_gemini()

    def _init_gemini(self):
        """Initialize Gemini API with proper error handling"""
        try:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                logger.warning(
                    "GEMINI_API_KEY not found in environment variables")
                return

            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel("gemini-1.5-flash")
            logger.info("Gemini API initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Gemini API: {e}")
            self.model = None

    async def cog_load(self):
        """Called when the cog is loaded"""
        self.idle_task = self.bot.loop.create_task(self._idle_watcher())
        logger.info("RoleplaySQL cog loaded")

    async def cog_unload(self):
        """Called when the cog is unloaded"""
        if self.idle_task:
            self.idle_task.cancel()
        logger.info("RoleplaySQL cog unloaded")

    def get_rp_channel_id(self):
        """Helper method to get RP channel ID from config"""
        try:
            conn = db_connect()
            cur = conn.cursor()
            cur.execute("SELECT value FROM config WHERE key='rp_channel_id'")
            row = cur.fetchone()
            conn.close()
            return int(row[0]) if row else None
        except (sqlite3.Error, ValueError) as e:
            logger.error(f"Error getting RP channel ID: {e}")
            return None

    async def _idle_watcher(self):
        """Background task to monitor idle sessions"""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                conn = db_connect()
                cur = conn.cursor()
                cur.execute(
                    "SELECT user_id, channel_id, last_active FROM sessions")
                rows = cur.fetchall()
                now = datetime.utcnow()

                for row in rows:
                    try:
                        last_active = datetime.fromisoformat(
                            row["last_active"])
                        if now - last_active > self.idle_timeout:
                            channel = self.bot.get_channel(row["channel_id"])
                            if channel:
                                await channel.send(
                                    "⏲️ Session ended due to inactivity.")
                            cur.execute("DELETE FROM sessions WHERE user_id=?",
                                        (row["user_id"], ))
                            logger.info(
                                f"Ended idle session for user {row['user_id']}"
                            )
                    except Exception as e:
                        logger.error(
                            f"Error processing idle session for user {row['user_id']}: {e}"
                        )

                conn.commit()
                conn.close()

            except Exception as e:
                logger.error(f"Error in idle watcher: {e}")

            await asyncio.sleep(30)

    @app_commands.command(name="setrpchannel",
                          description="Set the roleplay channel (Admin only)")
    @app_commands.default_permissions(administrator=True)
    async def setrpchannel(self, interaction: discord.Interaction,
                           channel: discord.TextChannel):
        try:
            conn = db_connect()
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES ('rp_channel_id', ?)",
                (str(channel.id), ))
            conn.commit()
            conn.close()
            await interaction.response.send_message(
                f"✅ RP channel set to {channel.mention}", ephemeral=True)
            logger.info(
                f"RP channel set to {channel.id} by {interaction.user}")

        except Exception as e:
            logger.error(f"Error setting RP channel: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while setting the RP channel.",
                ephemeral=True)

    @app_commands.command(name="addcharacter",
                          description="Add a character to the database")
    async def addcharacter(self, interaction: discord.Interaction, name: str,
                           prompt: str):
        # Validation
        if len(name) > 50:
            await interaction.response.send_message(
                "❌ Character name too long (max 50 characters).",
                ephemeral=True)
            return

        if len(prompt) > 2000:
            await interaction.response.send_message(
                "❌ Character prompt too long (max 2000 characters).",
                ephemeral=True)
            return

        if not name.strip():
            await interaction.response.send_message(
                "❌ Character name cannot be empty.", ephemeral=True)
            return

        try:
            conn = db_connect()
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO characters (name, prompt) VALUES (?, ?)",
                (name.lower().strip(), prompt.strip()))
            conn.commit()
            conn.close()

            await interaction.response.send_message(
                f"✅ Character `{name}` added successfully.", ephemeral=True)
            logger.info(f"Character '{name}' added by {interaction.user}")

        except sqlite3.Error as e:
            logger.error(f"Database error adding character: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while adding the character.",
                ephemeral=True)

    @app_commands.command(name="listcharacters",
                          description="List all available characters")
    async def listcharacters(self, interaction: discord.Interaction):
        try:
            conn = db_connect()
            cur = conn.cursor()
            cur.execute("SELECT name FROM characters ORDER BY name")
            rows = cur.fetchall()
            conn.close()

            if not rows:
                await interaction.response.send_message(
                    "❌ No characters found in the database.", ephemeral=True)
                return

            character_list = ", ".join([row[0].title() for row in rows])
            embed = discord.Embed(title="Available Characters",
                                  description=character_list,
                                  color=0x00ff00)
            await interaction.response.send_message(embed=embed,
                                                    ephemeral=True)

        except sqlite3.Error as e:
            logger.error(f"Database error listing characters: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while fetching characters.",
                ephemeral=True)

    @app_commands.command(name="roleplay",
                          description="Start a roleplay session")
    async def roleplay(self, interaction: discord.Interaction, character: str):
        # Check if RP channel is configured
        rp_channel_id = self.get_rp_channel_id()
        if not rp_channel_id:
            await interaction.response.send_message(
                "❌ No RP channel configured. Ask an admin to set one.",
                ephemeral=True)
            return

        # Check if user is in the correct channel
        if interaction.channel_id != rp_channel_id:
            channel = self.bot.get_channel(rp_channel_id)
            channel_mention = channel.mention if channel else "the configured RP channel"
            await interaction.response.send_message(
                f"❌ Use this command in {channel_mention}.", ephemeral=True)
            return

        try:
            conn = db_connect()
            cur = conn.cursor()

            # Check if user already has an active session
            cur.execute("SELECT * FROM sessions WHERE user_id=?",
                        (interaction.user.id, ))
            existing_session = cur.fetchone()

            if existing_session:
                conn.close()
                await interaction.response.send_message(
                    "❌ You already have an active roleplay session. Use `/endroleplay` first.",
                    ephemeral=True)
                return

            # Check if character exists
            cur.execute("SELECT prompt FROM characters WHERE name=?",
                        (character.lower().strip(), ))
            row = cur.fetchone()
            if not row:
                conn.close()
                await interaction.response.send_message(
                    f"❌ Character `{character}` not found. Use `/listcharacters` to see available characters.",
                    ephemeral=True)
                return

            # Create thread
            channel = interaction.channel
            thread_name = f"RP: {interaction.user.display_name} as {character.title()}"
            # Ensure thread name isn't too long
            if len(thread_name) > 100:
                thread_name = thread_name[:97] + "..."

            thread = await channel.create_thread(
                name=thread_name, type=discord.ChannelType.public_thread)

            # Store session
            cur.execute(
                "INSERT INTO sessions (user_id, character, channel_id, last_active) VALUES (?, ?, ?, ?)",
                (interaction.user.id, character.lower().strip(), thread.id,
                 datetime.utcnow().isoformat()))
            conn.commit()
            conn.close()

            await interaction.response.send_message(
                f"✅ Roleplay started with **{character.title()}** in {thread.mention}",
                ephemeral=True)

            # Send initial message in thread
            embed = discord.Embed(
                title=f"🎭 Roleplay Session: {character.title()}",
                description=
                f"**Player:** {interaction.user.mention}\n**Character:** {character.title()}",
                color=0x9932cc)

            # Truncate prompt if too long for embed
            prompt_display = row['prompt']
            if len(prompt_display) > 1024:
                prompt_display = prompt_display[:1021] + "..."

            embed.add_field(name="Character Prompt",
                            value=prompt_display,
                            inline=False)
            embed.set_footer(
                text=
                "Type messages to roleplay! Session will end after 5 minutes of inactivity."
            )

            await thread.send(embed=embed)
            logger.info(
                f"Roleplay session started: {interaction.user} as {character}")

        except discord.HTTPException as e:
            logger.error(f"Discord API error creating roleplay session: {e}")
            await interaction.response.send_message(
                "❌ Error creating thread. Please try again.", ephemeral=True)
        except sqlite3.Error as e:
            logger.error(f"Database error creating roleplay session: {e}")
            await interaction.response.send_message(
                "❌ Database error occurred. Please try again.", ephemeral=True)
        except Exception as e:
            logger.error(f"Unexpected error creating roleplay session: {e}")
            await interaction.response.send_message(
                f"❌ An unexpected error occurred: {str(e)}", ephemeral=True)

    @app_commands.command(name="endroleplay",
                          description="End your current roleplay session")
    async def endroleplay(self, interaction: discord.Interaction):
        try:
            conn = db_connect()
            cur = conn.cursor()
            cur.execute("SELECT channel_id FROM sessions WHERE user_id=?",
                        (interaction.user.id, ))
            session = cur.fetchone()

            if not session:
                conn.close()
                await interaction.response.send_message(
                    "❌ You don't have an active roleplay session.",
                    ephemeral=True)
                return

            cur.execute("DELETE FROM sessions WHERE user_id=?",
                        (interaction.user.id, ))
            conn.commit()
            conn.close()

            # Try to send end message to the thread
            try:
                channel = self.bot.get_channel(session['channel_id'])
                if channel:
                    await channel.send("🎭 **Roleplay session ended by user.**")
            except Exception as e:
                logger.error(
                    f"Error sending end message to channel {session['channel_id']}: {e}"
                )

            await interaction.response.send_message(
                "✅ Roleplay session ended successfully.", ephemeral=True)
            logger.info(f"Roleplay session ended by {interaction.user}")

        except sqlite3.Error as e:
            logger.error(f"Database error ending roleplay session: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while ending the session.",
                ephemeral=True)

    async def generate_character_response(self, character_name, user_message,
                                          conversation_history):
        """Generate AI response using Gemini"""
        if self.model is None:
            return "🤖 AI is not available right now. Please check the configuration."

        try:
            # Get character prompt
            conn = db_connect()
            cur = conn.cursor()
            cur.execute("SELECT prompt FROM characters WHERE name=?",
                        (character_name, ))
            character_row = cur.fetchone()
            conn.close()

            if not character_row:
                return "❌ Character not found."

            # Build conversation context
            system_prompt = f"""You are roleplaying as a character. Here is your character description:
{character_row['prompt']}

IMPORTANT ROLEPLAY RULES:
- Stay in character at all times
- Respond as this character would respond
- Keep responses conversational and engaging
- Don't break character or mention that you're an AI
- Respond in first person as the character
- Keep responses under 400 characters for Discord
- Be appropriate for all audiences

Recent conversation context:"""

            # Add recent conversation history (last 6 messages to avoid token limits)
            context_messages = []
            for msg in conversation_history[-6:]:
                if msg['role'] == 'user':
                    context_messages.append(f"User: {msg['content']}")
                elif msg['role'] == 'assistant':
                    context_messages.append(
                        f"{character_name}: {msg['content']}")

            if context_messages:
                full_prompt = system_prompt + "\n" + "\n".join(
                    context_messages
                ) + f"\n\nUser: {user_message}\n{character_name}:"
            else:
                full_prompt = system_prompt + f"\n\nUser: {user_message}\n{character_name}:"

            logger.info(
                f"Generating response for {character_name} (prompt length: {len(full_prompt)})"
            )

            # Generate response with timeout
            response = await asyncio.wait_for(asyncio.to_thread(
                self.model.generate_content, full_prompt),
                                              timeout=30.0)

            if response and hasattr(response, 'text') and response.text:
                result = response.text.strip()

                # Ensure response isn't too long for Discord
                if len(result) > 2000:
                    result = result[:1997] + "..."

                logger.info(
                    f"Generated response for {character_name}: {len(result)} characters"
                )
                return result
            else:
                logger.warning("Empty response from Gemini")
                return "I seem to be at a loss for words right now..."

        except asyncio.TimeoutError:
            logger.error("Timeout generating character response")
            return "I'm taking too long to think. Perhaps try again?"
        except Exception as e:
            logger.error(f"Error generating character response: {e}")
            return "I'm having some trouble speaking right now. Perhaps try again in a moment?"

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore bot messages
        if message.author.bot:
            return

        try:
            # Check if this is a roleplay channel
            conn = db_connect()
            cur = conn.cursor()
            cur.execute("SELECT * FROM sessions WHERE channel_id=?",
                        (message.channel.id, ))
            session = cur.fetchone()

            if session:
                # Update last activity
                cur.execute(
                    "UPDATE sessions SET last_active=? WHERE user_id=?",
                    (datetime.utcnow().isoformat(), session["user_id"]))

                # Save user message to history
                cur.execute(
                    "INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)",
                    (message.author.id, "user", message.content))
                conn.commit()

                # Get conversation history for context
                cur.execute(
                    "SELECT role, content FROM history WHERE user_id=? ORDER BY ts DESC LIMIT 10",
                    (session["user_id"], ))
                history_rows = cur.fetchall()
                conversation_history = [{
                    "role": row[0],
                    "content": row[1]
                } for row in reversed(history_rows)]

                conn.close()

                # Generate character response
                if self.model:
                    async with message.channel.typing():
                        character_response = await self.generate_character_response(
                            session["character"], message.content,
                            conversation_history)

                    if character_response:
                        # Send character response
                        bot_message = await message.channel.send(
                            character_response)

                        # Save bot response to history
                        conn = db_connect()
                        cur = conn.cursor()
                        cur.execute(
                            "INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)",
                            (session["user_id"], "assistant",
                             character_response))
                        conn.commit()
                        conn.close()
                else:
                    # Model not available, send a fallback message
                    await message.channel.send(
                        "🤖 *The character seems to be away right now. AI is not configured.*"
                    )
            else:
                conn.close()

        except Exception as e:
            logger.error(f"Error handling message: {e}")

    @app_commands.command(name="clearhistory",
                          description="Clear roleplay history (Admin only)")
    @app_commands.default_permissions(administrator=True)
    async def clearhistory(self,
                           interaction: discord.Interaction,
                           user: discord.Member = None):
        try:
            conn = db_connect()
            cur = conn.cursor()

            if user:
                cur.execute("DELETE FROM history WHERE user_id=?", (user.id, ))
                await interaction.response.send_message(
                    f"✅ Cleared roleplay history for {user.mention}",
                    ephemeral=True)
                logger.info(
                    f"Cleared history for user {user.id} by {interaction.user}"
                )
            else:
                cur.execute("DELETE FROM history")
                await interaction.response.send_message(
                    "✅ Cleared all roleplay history", ephemeral=True)
                logger.info(f"Cleared all history by {interaction.user}")

            conn.commit()
            conn.close()

        except sqlite3.Error as e:
            logger.error(f"Database error clearing history: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while clearing history.", ephemeral=True)


# Setup function for loading the cog
async def setup(bot: commands.Bot):
    """Setup function called when loading the cog"""
    await bot.add_cog(RoleplaySQL(bot))
    logger.info("RoleplaySQL cog loaded successfully!")


# Optional teardown function
async def teardown(bot: commands.Bot):
    """Teardown function called when unloading the cog (optional)"""
    logger.info("RoleplaySQL cog unloaded!")
