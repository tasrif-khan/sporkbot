import psutil
import logging
from datetime import datetime

class BotMonitor:
    def __init__(self, bot):
        self.bot = bot
        self.start_time = datetime.now()

    async def get_system_stats(self):
        """Get system resource usage"""
        return {
            'cpu_percent': psutil.cpu_percent(),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_usage': psutil.disk_usage('/').percent,
            'guild_count': len(self.bot.guilds),
            'active_voice_connections': len(self.bot.voice_clients),
            'uptime': (datetime.now() - self.start_time).total_seconds()
        }

    async def log_stats(self):
        """Log system stats periodically"""
        stats = await self.get_system_stats()
        logging.info(f"System Stats: {stats}")