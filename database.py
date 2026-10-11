import sqlite3
import logging
from contextlib import closing
from datetime import datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class Database:
    """Database handler for Discord bot data storage"""
    
    def __init__(self, db_path: str = "bot_data.db"):
        self.db_path = db_path
        self.init_database()
        
    def init_database(self):
        """Initialize database tables"""
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                cursor = conn.cursor()
                cursor.execute("BEGIN")
                
                # ``verified_users`` was originally keyed only by user_id.  Move
                # those rows to a reserved legacy guild rather than allowing a
                # verification from one guild to leak into every guild.
                cursor.execute("PRAGMA table_info(verified_users)")
                verified_columns = {row[1] for row in cursor.fetchall()}
                if verified_columns and "guild_id" not in verified_columns:
                    cursor.execute("ALTER TABLE verified_users RENAME TO verified_users_legacy")

                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS verified_users (
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        verification_method TEXT DEFAULT 'reaction',
                        PRIMARY KEY (guild_id, user_id)
                    )
                ''')
                if verified_columns and "guild_id" not in verified_columns:
                    cursor.execute('''
                        INSERT OR IGNORE INTO verified_users
                            (guild_id, user_id, verified_at, verification_method)
                        SELECT 0, user_id, verified_at, verification_method
                        FROM verified_users_legacy
                    ''')
                    cursor.execute("DROP TABLE verified_users_legacy")
                
                # Create server_settings table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS server_settings (
                        guild_id INTEGER PRIMARY KEY,
                        guild_name TEXT,
                        verification_channel_id INTEGER,
                        verification_role_id INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                # Create verification_attempts table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS verification_attempts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        username TEXT,
                        success BOOLEAN,
                        reason TEXT,
                        timestamp TIMESTAMP,
                        ip_hash TEXT
                    )
                ''')
                cursor.execute("PRAGMA table_info(verification_attempts)")
                attempt_columns = {row[1] for row in cursor.fetchall()}
                if "guild_id" not in attempt_columns:
                    cursor.execute("ALTER TABLE verification_attempts ADD COLUMN guild_id INTEGER")
                
                conn.commit()
                logger.info("Database initialized successfully")
                
        except sqlite3.Error as e:
            logger.error(f"Database initialization error: {e}")
            
    def is_user_verified(self, user_id: int, guild_id: Optional[int] = None) -> bool:
        """Check if a user is verified"""
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                cursor = conn.cursor()
                # None is retained for callers using the old API and addresses
                # only migrated/unscoped legacy rows (guild_id 0).
                scoped_guild_id = guild_id if guild_id is not None else 0
                cursor.execute(
                    "SELECT user_id FROM verified_users WHERE guild_id = ? AND user_id = ?",
                    (scoped_guild_id, user_id),
                )
                return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"Error checking user verification: {e}")
            return False
            
    def verify_user(self, user_id: int, guild_id: Optional[int] = None, method: str = 'reaction'):
        """Mark a user as verified"""
        try:
            if isinstance(guild_id, str) and method == 'reaction':
                # Preserve the historical verify_user(user_id, method) call.
                method, guild_id = guild_id, None
            with closing(sqlite3.connect(self.db_path)) as conn:
                cursor = conn.cursor()
                scoped_guild_id = guild_id if guild_id is not None else 0
                cursor.execute('''
                    INSERT OR REPLACE INTO verified_users
                        (guild_id, user_id, verification_method, verified_at)
                    VALUES (?, ?, ?, ?)
                ''', (scoped_guild_id, user_id, method, datetime.now().isoformat(' ')))
                conn.commit()
                logger.info("User %s verified in guild %s", user_id, scoped_guild_id)
        except sqlite3.Error as e:
            logger.error(f"Error verifying user: {e}")
            
    def add_verified_user(self, user_id: int, guild_id: Optional[int] = None, method: str = 'reaction'):
        """Add a verified user (alias for verify_user for compatibility)"""
        if isinstance(guild_id, str) and method == 'reaction':
            method, guild_id = guild_id, None
        self.verify_user(user_id, guild_id, method)
        
    def log_verification_attempt(self, user_id: int, username: str, success: bool, reason: str, timestamp: datetime, ip_hash: str = None, guild_id: Optional[int] = None):
        """Log a verification attempt"""
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO verification_attempts
                        (user_id, username, success, reason, timestamp, ip_hash, guild_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, username, success, reason, timestamp, ip_hash, guild_id))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error logging verification attempt: {e}")
            
    def get_server_settings(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """Get server settings for a guild"""
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM server_settings WHERE guild_id = ?", (guild_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"Error getting server settings: {e}")
            return None
            
    def update_server_settings(self, guild_id: int, **kwargs):
        """Update server settings with a strict field whitelist."""
        allowed_fields = {
            "guild_name",
            "verification_channel_id",
            "verification_role_id",
        }
        unknown_fields = set(kwargs) - allowed_fields
        if unknown_fields:
            raise ValueError(
                f"Unsupported server setting fields: {sorted(unknown_fields)}"
            )

        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                cursor = conn.cursor()
                
                # Get existing settings
                cursor.execute("SELECT * FROM server_settings WHERE guild_id = ?", (guild_id,))
                existing = cursor.fetchone()
                
                if existing:
                    # Update existing record
                    set_clauses = []
                    values = []
                    for key, value in kwargs.items():
                        set_clauses.append(f"{key} = ?")
                        values.append(value)
                    
                    set_clauses.append("updated_at = ?")
                    values.append(datetime.now())
                    values.append(guild_id)
                    
                    query = f"UPDATE server_settings SET {', '.join(set_clauses)} WHERE guild_id = ?"
                    cursor.execute(query, values)
                else:
                    # Insert new record
                    kwargs['guild_id'] = guild_id
                    kwargs['created_at'] = datetime.now()
                    kwargs['updated_at'] = datetime.now()
                    
                    columns = list(kwargs.keys())
                    placeholders = ['?' for _ in columns]
                    values = list(kwargs.values())
                    
                    query = f"INSERT INTO server_settings ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
                    cursor.execute(query, values)
                
                conn.commit()
                logger.info(f"Server settings updated for guild {guild_id}")
                
        except sqlite3.Error as e:
            logger.error(f"Error updating server settings: {e}")
            
    def set_verification_role(self, guild_id: int, role_id: int):
        """Set verification role for a server"""
        self.update_server_settings(guild_id, verification_role_id=role_id)
        
    def get_verification_stats(self, guild_id: Optional[int] = None) -> Dict[str, Any]:
        """Get verification statistics"""
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                cursor = conn.cursor()
                
                scope = " WHERE guild_id = ?" if guild_id is not None else ""
                params = (guild_id,) if guild_id is not None else ()
                cursor.execute(f"SELECT COUNT(*) FROM verified_users{scope}", params)
                total_verified = cursor.fetchone()[0]
                
                # Get total verification attempts
                attempt_scope = " WHERE guild_id = ?" if guild_id is not None else ""
                cursor.execute(f"SELECT COUNT(*) FROM verification_attempts{attempt_scope}", params)
                total_attempts = cursor.fetchone()[0]
                
                # Get successful attempts
                success_scope = " WHERE success = 1 AND guild_id = ?" if guild_id is not None else " WHERE success = 1"
                cursor.execute(f"SELECT COUNT(*) FROM verification_attempts{success_scope}", params)
                successful_attempts = cursor.fetchone()[0]
                
                # Get recent verifications (last 7 days)
                recent_scope = " WHERE verified_at >= datetime('now', '-7 days') AND guild_id = ?" if guild_id is not None else " WHERE verified_at >= datetime('now', '-7 days')"
                cursor.execute(f"SELECT COUNT(*) FROM verified_users{recent_scope}", params)
                recent_verifications = cursor.fetchone()[0]

                failed_scope = " AND guild_id = ?" if guild_id is not None else ""
                cursor.execute(
                    "SELECT COUNT(*) FROM verification_attempts "
                    "WHERE success = 0 AND datetime(timestamp) >= datetime('now', '-1 day')"
                    + failed_scope, params,
                )
                failed_24h = cursor.fetchone()[0]
                
                return {
                    'total_verified': total_verified,
                    'total_attempts': total_attempts,
                    'successful_attempts': successful_attempts,
                    'recent_verifications': recent_verifications,
                    'failed_24h': failed_24h,
                    'success_rate': (successful_attempts / total_attempts * 100) if total_attempts > 0 else 0
                }
                
        except sqlite3.Error as e:
            logger.error(f"Error getting verification stats: {e}")
            return {
                'total_verified': 0,
                'total_attempts': 0,
                'successful_attempts': 0,
                'recent_verifications': 0,
                'failed_24h': 0,
                'success_rate': 0
            }
