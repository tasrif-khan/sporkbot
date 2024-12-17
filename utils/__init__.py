"""
SporkMP3 Bot Utilities
---------------------
This package contains utility classes and functions for the SporkMP3 Discord bot.

Main components:
- AudioTrack: Represents a single audio track with download and playback capabilities
- TrackManager: Manages the queue and handles file cleanup

Beta Version: 0.1.0
"""
from .monitoring import BotMonitor
from .track_manager import AudioTrack, TrackManager

__all__ = ['AudioTrack', 'TrackManager', 'BotMonitor']

# Additional metadata
__version__ = '0.1.0'
__author__ = 'Spike/Guntware'
__description__ = 'Utility package for SporkMP3 Discord Bot'