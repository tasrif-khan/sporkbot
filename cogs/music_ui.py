import discord
import time
from datetime import timedelta

class MusicUI:
    """Handles UI elements like embeds and formatting"""
    def __init__(self, emoji_dict):
        self.emoji = emoji_dict
    
    def create_embed(self, title, description, color=discord.Color.blue()):
        """Helper method to create consistent embeds"""
        embed = discord.Embed(
            title=title,
            description=description,
            color=color
        )
        embed.set_footer(text=f"SporkMP3 Bot • {time.strftime('%H:%M:%S')}")
        return embed
    
    def format_duration(self, seconds):
        """Format duration in a consistent way"""
        if seconds < 3600:  # Less than 1 hour
            return time.strftime('%M:%S', time.gmtime(seconds))
        return time.strftime('%H:%M:%S', time.gmtime(seconds))
    
    def create_progress_bar(self, position, duration, length=20):
        """Create a progress bar for track position"""
        progress = int((position / duration) * length)
        return f"{'▰' * progress}{'▱' * (length - progress)}"
