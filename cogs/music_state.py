import time
import logging
from typing import Dict, List, Optional

class GuildState:
    """Stores the state for a specific guild"""
    def __init__(self):
        self.queue = []
        self.current_track = None
        self.volume = 100
        self.is_seeking = False
        self.last_activity = time.time()
        self.loop_enabled = False
        self.loop_count = 0
        self.max_loops = None  # None means infinite loop
        self.last_channel_id = None  # Track the last channel used for music commands

class MusicState:
    """Manages music state across all guilds"""
    def __init__(self):
        self.guild_states = {}  # Store states for each guild
        self.alone_since = {}  # Track time since bot was left alone
        self.rate_limits = {}  # Rate limiting state
    
    async def get_guild_state(self, guild_id: int) -> GuildState:
        """Get or create guild state"""
        if guild_id not in self.guild_states:
            self.guild_states[guild_id] = GuildState()
        self.guild_states[guild_id].last_activity = time.time()
        return self.guild_states[guild_id]
    
    async def check_rate_limit(self, guild_id: int) -> bool:
        """Basic rate limiting per guild"""
        current_time = time.time()
        if guild_id in self.rate_limits:
            if current_time - self.rate_limits[guild_id] < 2:  # 2 second cooldown
                return False
        self.rate_limits[guild_id] = current_time
        return True