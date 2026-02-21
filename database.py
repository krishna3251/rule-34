import sqlite3
import logging
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
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Create verified_users table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS verified_users (
                        user_id INTEGER PRIMARY KEY,
                        verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        verification_method TEXT DEFAULT 'reaction'
                    )
                ''')
                
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
                
                conn.commit()
                logger.info("Database initialized successfully")
                
        except sqlite3.Error as e:
            logger.error(f"Database initialization error: {e}")
            
    def is_user_verified(self, user_id: int) -> bool:
        """Check if a user is verified"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM verified_users WHERE user_id = ?", (user_id,))
                return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"Error checking user verification: {e}")
            return False
            
    def verify_user(self, user_id: int, method: str = 'reaction'):
        """Mark a user as verified"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO verified_users (user_id, verification_method, verified_at)
                    VALUES (?, ?, ?)
                ''', (user_id, method, datetime.now()))
                conn.commit()
                logger.info(f"User {user_id} verified successfully")
        except sqlite3.Error as e:
            logger.error(f"Error verifying user: {e}")
            
    def add_verified_user(self, user_id: int, method: str = 'reaction'):
        """Add a verified user (alias for verify_user for compatibility)"""
        self.verify_user(user_id, method)
        
    def log_verification_attempt(self, user_id: int, username: str, success: bool, reason: str, timestamp: datetime, ip_hash: str = None):
        """Log a verification attempt"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO verification_attempts (user_id, username, success, reason, timestamp, ip_hash)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (user_id, username, success, reason, timestamp, ip_hash))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error logging verification attempt: {e}")
            
    def get_server_settings(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """Get server settings for a guild"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM server_settings WHERE guild_id = ?", (guild_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"Error getting server settings: {e}")
            return None
            
    def update_server_settings(self, guild_id: int, **kwargs):
        """Update server settings"""
        try:
            with sqlite3.connect(self.db_path) as conn:
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
        
    def get_verification_stats(self) -> Dict[str, Any]:
        """Get verification statistics"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Get total verified users
                cursor.execute("SELECT COUNT(*) FROM verified_users")
                total_verified = cursor.fetchone()[0]
                
                # Get total verification attempts
                cursor.execute("SELECT COUNT(*) FROM verification_attempts")
                total_attempts = cursor.fetchone()[0]
                
                # Get successful attempts
                cursor.execute("SELECT COUNT(*) FROM verification_attempts WHERE success = 1")
                successful_attempts = cursor.fetchone()[0]
                
                # Get recent verifications (last 7 days)
                cursor.execute("SELECT COUNT(*) FROM verified_users WHERE verified_at >= datetime('now', '-7 days')")
                recent_verifications = cursor.fetchone()[0]
                
                return {
                    'total_verified': total_verified,
                    'total_attempts': total_attempts,
                    'successful_attempts': successful_attempts,
                    'recent_verifications': recent_verifications,
                    'success_rate': (successful_attempts / total_attempts * 100) if total_attempts > 0 else 0
                }
                
        except sqlite3.Error as e:
            logger.error(f"Error getting verification stats: {e}")
            return {
                'total_verified': 0,
                'total_attempts': 0,
                'successful_attempts': 0,
                'recent_verifications': 0,
                'success_rate': 0
            }