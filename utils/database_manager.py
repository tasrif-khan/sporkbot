import sqlite3
import logging
from typing import List, Optional

class DatabaseManager:
    def __init__(self, db_path: str = 'bot_settings.db'):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        """Initialize database tables"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Create guild settings table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS guild_settings (
                        guild_id INTEGER PRIMARY KEY,
                        autoplay_enabled BOOLEAN DEFAULT 1,
                        autodisconnect_enabled BOOLEAN DEFAULT 1
                    )
                ''')
                
                # Create blacklisted users table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS blacklisted_users (
                        guild_id INTEGER,
                        user_id INTEGER,
                        PRIMARY KEY (guild_id, user_id)
                    )
                ''')
                
                # Create whitelisted roles table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS whitelisted_roles (
                        guild_id INTEGER,
                        role_id INTEGER,
                        PRIMARY KEY (guild_id, role_id)
                    )
                ''')
                
                conn.commit()
                logging.info("Database initialized successfully")
        except Exception as e:
            logging.error(f"Error initializing database: {e}")
            raise

    # Autoplay settings
    def get_autoplay_setting(self, guild_id: int) -> bool:
        """Get autoplay setting for a guild"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT autoplay_enabled FROM guild_settings WHERE guild_id = ?",
                    (guild_id,)
                )
                result = cursor.fetchone()
                return bool(result[0]) if result else True  # Default to True if not set
        except Exception as e:
            logging.error(f"Error getting autoplay setting: {e}")
            return True

    def set_autoplay_setting(self, guild_id: int, enabled: bool):
        """Set autoplay setting for a guild"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO guild_settings (guild_id, autoplay_enabled)
                    VALUES (?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET autoplay_enabled = ?
                ''', (guild_id, enabled, enabled))
                conn.commit()
        except Exception as e:
            logging.error(f"Error setting autoplay: {e}")
            raise

    # Blacklist management
    def add_to_blacklist(self, guild_id: int, user_id: int):
        """Add a user to the blacklist"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR IGNORE INTO blacklisted_users (guild_id, user_id) VALUES (?, ?)",
                    (guild_id, user_id)
                )
                conn.commit()
        except Exception as e:
            logging.error(f"Error adding user to blacklist: {e}")
            raise

    def remove_from_blacklist(self, guild_id: int, user_id: int):
        """Remove a user from the blacklist"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM blacklisted_users WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id)
                )
                conn.commit()
        except Exception as e:
            logging.error(f"Error removing user from blacklist: {e}")
            raise

    def is_user_blacklisted(self, guild_id: int, user_id: int) -> bool:
        """Check if a user is blacklisted"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM blacklisted_users WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id)
                )
                return cursor.fetchone() is not None
        except Exception as e:
            logging.error(f"Error checking blacklist: {e}")
            return False

    def get_blacklisted_users(self, guild_id: int) -> List[int]:
        """Get all blacklisted users for a guild"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT user_id FROM blacklisted_users WHERE guild_id = ?",
                    (guild_id,)
                )
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logging.error(f"Error getting blacklisted users: {e}")
            return []

    # Role whitelist management
    def add_to_role_whitelist(self, guild_id: int, role_id: int):
        """Add a role to the whitelist"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR IGNORE INTO whitelisted_roles (guild_id, role_id) VALUES (?, ?)",
                    (guild_id, role_id)
                )
                conn.commit()
        except Exception as e:
            logging.error(f"Error adding role to whitelist: {e}")
            raise

    def remove_from_role_whitelist(self, guild_id: int, role_id: int):
        """Remove a role from the whitelist"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM whitelisted_roles WHERE guild_id = ? AND role_id = ?",
                    (guild_id, role_id)
                )
                conn.commit()
        except Exception as e:
            logging.error(f"Error removing role from whitelist: {e}")
            raise

    def get_whitelisted_roles(self, guild_id: int) -> List[int]:
        """Get all whitelisted roles for a guild"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT role_id FROM whitelisted_roles WHERE guild_id = ?",
                    (guild_id,)
                )
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logging.error(f"Error getting whitelisted roles: {e}")
            return []

    def has_whitelisted_roles(self, guild_id: int) -> bool:
        """Check if a guild has any whitelisted roles"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM whitelisted_roles WHERE guild_id = ? LIMIT 1",
                    (guild_id,)
                )
                return cursor.fetchone() is not None
        except Exception as e:
            logging.error(f"Error checking whitelisted roles: {e}")
            return False

    def get_autodisconnect_setting(self, guild_id: int) -> bool:
        """Get autodisconnect setting for a guild"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT autodisconnect_enabled FROM guild_settings WHERE guild_id = ?",
                    (guild_id,)
                )
                result = cursor.fetchone()
                return bool(result[0]) if result else True  # Default to True if not set
        except Exception as e:
            logging.error(f"Error getting autodisconnect setting: {e}")
            return True

    def set_autodisconnect_setting(self, guild_id: int, enabled: bool):
        """Set autodisconnect setting for a guild"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO guild_settings (guild_id, autodisconnect_enabled)
                    VALUES (?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET autodisconnect_enabled = ?
                ''', (guild_id, enabled, enabled))
                conn.commit()
        except Exception as e:
            logging.error(f"Error setting autodisconnect: {e}")
            raise