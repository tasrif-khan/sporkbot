import discord
import asyncio
import logging
import time
from datetime import timedelta
import os
from utils.track_manager import AudioTrack, TrackManager
from utils.database_manager import DatabaseManager

class MusicPlayback:
    """Handles audio playback functionality"""
    def __init__(self, bot, track_manager, music_state, db, music_ui, ffmpeg_path='ffmpeg'):
        self.bot = bot
        self.track_manager = track_manager
        self.music_state = music_state
        self.db = db
        self.music_ui = music_ui  # Added music_ui for creating embeds
        self.ffmpeg_path = ffmpeg_path
    
    def get_pcm_audio(self, track, start_time=0, speed=None):
        """Get PCM audio source for playback with variable bitrate and speed"""
        if track is None:
            logging.error("Cannot create audio source: track is None")
            raise ValueError("Track is None")
            
        # Check if track has necessary attributes
        if not hasattr(track, 'last_accessed'):
            logging.error("Track missing required 'last_accessed' attribute")
            raise ValueError("Track missing required attributes")
            
        track.last_accessed = time.time()
        
        # Ensure start_time is within valid range
        start_time = max(0, min(start_time, track.duration))
        
        # Format timestamp properly for FFmpeg
        timestamp = str(timedelta(seconds=int(start_time)))
        options = f'-ss {timestamp}'
        
        # Determine appropriate bitrate based on file type
        # Default to 192k but use higher for high-quality sources
        if hasattr(track, 'bitrate') and track.bitrate:
            original_bitrate = track.bitrate
            track.bitrate = min(320, track.bitrate)  # Clamp between 1-320
            if original_bitrate != track.bitrate:
                logging.info(f"Adjusted bitrate from {original_bitrate}kbps to {track.bitrate}kbps")
        elif track.downloaded_path.lower().endswith(('.flac', '.wav')):
            track.bitrate = 320  # Use highest bitrate for lossless formats
        else:
            track.bitrate = 192  # Default bitrate for most formats
        
        try:
            # If no speed provided, get from the database
            if speed is None:
                guild_id = None
                for guild in self.bot.guilds:
                    for vc in guild.voice_channels:
                        if guild.voice_client and guild.voice_client.channel == vc:
                            guild_id = guild.id
                            break
                if guild_id:
                    speed = self.db.get_playback_speed(guild_id)
                else:
                    speed = 100  # Default to normal speed
            
            # Apply speed filter if not 100%
            speed_filter = ""
            if speed != 100:
                speed_value = speed / 100.0
                # Use atempo filter for speed adjustment (with multi-stage for large changes)
                if speed_value > 2.0:
                    # FFmpeg atempo filter is limited to 0.5-2.0 range, so we chain filters
                    speed_filter = f"-filter:a \"atempo=2.0,atempo={speed_value/2.0}\""
                elif speed_value < 0.5:
                    # Similarly for very slow speeds
                    speed_filter = f"-filter:a \"atempo=0.5,atempo={speed_value/0.5}\""
                else:
                    speed_filter = f"-filter:a \"atempo={speed_value}\""
            
            # Handle various file formats including MP4
            ffmpeg_options = f'-vn -b:a {track.bitrate}k {speed_filter}'
            
            if track.downloaded_path.lower().endswith('.mp4'):
                audio_source = discord.FFmpegPCMAudio(
                    track.downloaded_path,
                    before_options=options,
                    executable=self.ffmpeg_path,
                    options=ffmpeg_options.strip()  # Extract audio and set bitrate
                )
            else:
                audio_source = discord.FFmpegPCMAudio(
                    track.downloaded_path,
                    before_options=options,
                    executable=self.ffmpeg_path,
                    options=ffmpeg_options.strip()  # Set bitrate for audio
                )
            
            logging.info(f"Created audio source with bitrate: {track.bitrate}k and speed: {speed}%")
            
            # Start tracking playback position
            track.start_playback(start_time)
            
            return discord.PCMVolumeTransformer(audio_source, volume=track.volume / 100)
        except Exception as e:
            logging.error(f"Error creating audio source: {e}")
            raise

    async def send_now_playing_message(self, guild, guild_state):
        """Send a 'Now Playing' message to the last used channel"""
        if not guild_state.last_channel_id:
            logging.info("No channel ID stored, can't send now playing message")
            return

        try:
            # Find the channel
            channel = guild.get_channel(guild_state.last_channel_id)
            if not channel:
                logging.warning(f"Channel {guild_state.last_channel_id} not found")
                return

            # Calculate current position - using the get_current_position method
            current_position = guild_state.current_track.get_current_position()
            
            # Create the progress bar
            progress_bar = self.music_ui.create_progress_bar(
                current_position,
                guild_state.current_track.duration
            )
            
            # Get the current speed setting
            speed = self.db.get_playback_speed(guild.id)
            speed_emoji = "🐌" if speed < 100 else "🚀" if speed > 100 else "⏱️"
            
            # Create the embed
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['play']} Now Playing",
                f"{self.music_ui.emoji['music']} **Track:** {guild_state.current_track.filename}\n"
                f"{self.music_ui.emoji['microphone']} **Requested by:** {guild_state.current_track.requester}\n"
                f"{self.music_ui.emoji['time']} **Duration:** {self.music_ui.format_duration(int(guild_state.current_track.duration))}\n"
                f"🎚️ **Bitrate:** {guild_state.current_track.bitrate}kbps\n"
                f"{speed_emoji} **Speed:** {speed}%\n"
                f"**Progress:** {progress_bar}",
                discord.Color.green()
            )
            
            # Add queue information if there are more tracks
            if guild_state.queue:
                next_track = guild_state.queue[0]
                embed.add_field(
                    name=f"{self.music_ui.emoji['queue']} Up Next",
                    value=f"{self.music_ui.emoji['music']} {next_track.filename}\n"
                        f"{self.music_ui.emoji['microphone']} Requested by: {next_track.requester}",
                    inline=False
                )
            
            await channel.send(embed=embed)
        except Exception as e:
            logging.error(f"Error sending now playing message: {e}")
    
    async def play_next(self, guild, force_play=False):
        """Play the next track in the queue"""
        guild_state = await self.music_state.get_guild_state(guild.id)
        if guild_state.is_seeking:
            return
                    
        try:
            # Handle looping - do this BEFORE cleaning up current track
            if guild_state.loop_enabled and guild_state.current_track:
                # Check if we've reached max loops
                if guild_state.max_loops is not None:
                    guild_state.loop_count += 1
                    if guild_state.loop_count >= guild_state.max_loops:
                        guild_state.loop_enabled = False  # Disable loop after reaching max
                        guild_state.loop_count = 0
                        guild_state.max_loops = None
                    else:
                        # Make a copy of current track for looping
                        current_track = guild_state.current_track
                        # Re-add current track to beginning of queue
                        guild_state.queue.insert(0, current_track)
                else:  # Infinite loop
                    # Make a copy of current track for looping
                    current_track = guild_state.current_track
                    # Re-add current track to beginning of queue
                    guild_state.queue.insert(0, current_track)
            
            # Clean up current track if exists
            if guild_state.current_track and not guild_state.is_seeking:
                self.track_manager.mark_file_inactive(guild_state.current_track.downloaded_path)
                guild_state.current_track.cleanup()
                guild_state.current_track = None

            # Check if queue is empty
            if not guild_state.queue:
                # Check autodisconnect setting and handle disconnection
                if self.db.get_autodisconnect_setting(guild.id):
                    voice_client = guild.voice_client
                    if voice_client and voice_client.is_connected():
                        try:
                            await voice_client.disconnect()
                            logging.info(f"Auto-disconnected from guild {guild.id} due to empty queue")
                        except Exception as e:
                            logging.error(f"Error during auto-disconnect in guild {guild.id}: {e}")
                return

            # Check autoplay setting - skip if disabled unless force_play is True
            if not force_play and not self.db.get_autoplay_setting(guild.id):
                return

            # Get voice client
            voice_client = guild.voice_client
            if not voice_client or not voice_client.is_connected():
                logging.warning(f"Voice client not connected in guild {guild.id}")
                return

            # Get next track and handle download
            try:
                guild_state.current_track = guild_state.queue.pop(0)
                self.track_manager.mark_file_active(guild_state.current_track.downloaded_path)
                
                # Download if not already downloaded
                if not guild_state.current_track.downloaded_path:
                    await self.track_manager.ensure_temp_folder()
                    await guild_state.current_track.download(self.bot.config['temp_folder'])
                    
                # Update last activity time
                guild_state.last_activity = time.time()
                
            except Exception as e:
                logging.error(f"Failed to prepare next track in guild {guild.id}: {e}")
                if guild_state.current_track:
                    self.track_manager.mark_file_inactive(guild_state.current_track.downloaded_path)
                guild_state.current_track = None
                # Try to play next track in queue if this one fails
                await self.play_next(guild, force_play)
                return

            # Clean up any old temporary files
            try:
                await self.track_manager.cleanup_temp_files()
            except Exception as e:
                logging.error(f"Error during temp file cleanup: {e}")

            # Create and configure audio source
            try:
                # Get the speed setting for this guild
                speed = self.db.get_playback_speed(guild.id)
                
                audio_source = self.get_pcm_audio(
                    guild_state.current_track, 
                    guild_state.current_track.position,
                    speed
                )
                
                # Set volume from guild state
                audio_source.volume = guild_state.volume / 100
                
            except Exception as e:
                logging.error(f"Error creating audio source in guild {guild.id}: {e}")
                if guild_state.current_track:
                    self.track_manager.mark_file_inactive(guild_state.current_track.downloaded_path)
                guild_state.current_track = None
                # Try to play next track in queue if this one fails
                await self.play_next(guild, force_play)
                return

            # Define after-playing callback
            def after_playing(error):
                if error:
                    logging.error(f'Player error in guild {guild.id}: {error}')
                
                # Reset alone timer if it exists
                self.music_state.alone_since.pop(guild.id, None)
                
                if not guild_state.is_seeking:
                    # Schedule next track
                    asyncio.run_coroutine_threadsafe(
                        self.play_next(guild), 
                        self.bot.loop
                    )

            # Start playback
            try:
                voice_client.play(audio_source, after=after_playing)
                logging.info(f"Started playing '{guild_state.current_track.filename}' in guild {guild.id}")
                
                # Reset alone timer if it exists since we're actively playing
                self.music_state.alone_since.pop(guild.id, None)
                
                # Send the now playing message if we have a channel to send to
                if guild_state.last_channel_id:
                    await self.send_now_playing_message(guild, guild_state)
                
            except Exception as e:
                logging.error(f"Error starting playback in guild {guild.id}: {e}")
                if guild_state.current_track:
                    self.track_manager.mark_file_inactive(guild_state.current_track.downloaded_path)
                guild_state.current_track = None
                # Try to play next track in queue if this one fails
                await self.play_next(guild, force_play)
                return

        except Exception as e:
            logging.error(f"Unexpected error in play_next for guild {guild.id}: {e}")
            # Clean up if there was an error
            try:
                if guild_state.current_track:
                    self.track_manager.mark_file_inactive(guild_state.current_track.downloaded_path)
                    guild_state.current_track.cleanup()
                    guild_state.current_track = None
            except Exception as cleanup_error:
                logging.error(f"Error during cleanup after playback failure in guild {guild.id}: {cleanup_error}")