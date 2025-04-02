import discord
import asyncio
import logging
import time
from utils.track_manager import AudioTrack

class MusicEvents:
    """Handles Discord event listeners for the music bot"""
    def __init__(self, bot, db, track_manager, music_state, music_ui, music_playback):
        self.bot = bot
        self.db = db
        self.track_manager = track_manager
        self.music_state = music_state
        self.music_ui = music_ui
        self.music_playback = music_playback
    
    async def periodic_cleanup(self):
        """Cleanup inactive guilds, temporary files, and stale alone timers"""
        while True:
            try:
                current_time = time.time()
                inactive_guilds = []
                cleaned_files = 0
                error_count = 0

                # 1. Clean up alone timers (5 minute threshold)
                for guild_id in list(self.music_state.alone_since.keys()):
                    try:
                        if current_time - self.music_state.alone_since[guild_id] > 300:  # 5 minutes
                            guild = self.bot.get_guild(guild_id)
                            if guild and guild.voice_client:
                                voice_channel = guild.voice_client.channel
                                if len(voice_channel.members) == 1:  # Still alone
                                    await guild.voice_client.disconnect()
                                    logging.info(f"Disconnected from guild {guild_id} after being alone for 5 minutes")
                                
                            self.music_state.alone_since.pop(guild_id, None)
                    except Exception as e:
                        logging.error(f"Error cleaning up alone timer for guild {guild_id}: {e}")
                        error_count += 1

                # 2. Clean up inactive guilds (1 hour threshold)
                for guild_id, state in list(self.music_state.guild_states.items()):
                    try:
                        if current_time - state.last_activity > 3600:  # 1 hour
                            # Clean up voice client if still connected
                            guild = self.bot.get_guild(guild_id)
                            if guild and guild.voice_client:
                                await guild.voice_client.disconnect()

                            # Clean up current track
                            if state.current_track:
                                state.current_track.cleanup()

                            # Clean up queued tracks
                            for track in state.queue:
                                track.cleanup()
                            
                            # Remove guild state
                            del self.music_state.guild_states[guild_id]
                            inactive_guilds.append(guild_id)
                            
                            logging.info(f"Cleaned up inactive guild {guild_id}")
                    except Exception as e:
                        logging.error(f"Error cleaning up inactive guild {guild_id}: {e}")
                        error_count += 1

                # 3. Clean up rate limits (60 second threshold)
                try:
                    self.music_state.rate_limits = {
                        guild_id: time for guild_id, time in self.music_state.rate_limits.items()
                        if current_time - time < 60
                    }
                except Exception as e:
                    logging.error(f"Error cleaning up rate limits: {e}")
                    error_count += 1

                # 4. Clean up temporary files
                try:
                    await self.track_manager.cleanup_temp_files()
                    cleaned_files += 1
                except Exception as e:
                    logging.error(f"Error cleaning up temporary files: {e}")
                    error_count += 1

                # Log cleanup summary if anything was cleaned
                if inactive_guilds or cleaned_files or error_count:
                    logging.info(
                        f"Cleanup completed: {len(inactive_guilds)} inactive guilds removed, "
                        f"{cleaned_files} temp file cleanups, "
                        f"{error_count} errors encountered"
                    )

                # Wait before next cleanup cycle (5 minutes)
                await asyncio.sleep(300)

            except Exception as e:
                logging.error(f"Error in periodic cleanup main loop: {e}")
                # If main loop encounters error, wait 1 minute before retrying
                await asyncio.sleep(60)
    
    async def on_voice_state_update(self, member, before, after):
        """Handle bot disconnection when alone in channel"""
        if member.bot:
            return

        if before.channel is not None:
            # Check if bot is in the channel that was left
            voice_client = before.channel.guild.voice_client
            if voice_client and voice_client.channel == before.channel:
                # Check if the bot is alone in the channel
                if len(before.channel.members) == 1:
                    # Record the time when bot was left alone
                    self.music_state.alone_since[before.channel.guild.id] = time.time()
                    
                    # Wait 5 minutes before checking again
                    await asyncio.sleep(300)  # 300 seconds = 5 minutes
                    
                    # Check if we're still alone after 5 minutes
                    current_voice_client = before.channel.guild.voice_client
                    if (current_voice_client and 
                        current_voice_client.channel == before.channel and 
                        len(before.channel.members) == 1):
                        
                        await current_voice_client.disconnect()
                        guild_state = await self.music_state.get_guild_state(before.channel.guild.id)
                        if guild_state.current_track:
                            guild_state.current_track.cleanup()
                        for track in guild_state.queue:
                            track.cleanup()
                        guild_state.queue.clear()
                        
                        # Clean up the alone_since entry
                        self.music_state.alone_since.pop(before.channel.guild.id, None)
                else:
                    # If we're not alone anymore, remove the alone_since entry
                    self.music_state.alone_since.pop(before.channel.guild.id, None)
    
    async def on_message(self, message):
        """Handle file uploads when bot is mentioned"""
        if message.author.bot:
            return

        bot_mention = f'<@{self.bot.user.id}>'
        if bot_mention in message.content:
            try:
                # Store the channel ID for this guild for now playing messages
                guild_state = await self.music_state.get_guild_state(message.guild.id)
                guild_state.last_channel_id = message.channel.id
                
                # Check bot permissions first
                if not message.channel.permissions_for(message.guild.me).send_messages:
                    logging.warning(f"Missing send message permissions in channel {message.channel.id}")
                    return

                # Check if bot can embed links
                can_embed = message.channel.permissions_for(message.guild.me).embed_links
                
                # Check if user is in voice channel
                if not message.author.voice:
                    if can_embed:
                        embed = self.music_ui.create_embed(
                            f"{self.music_ui.emoji['warning']} Voice Channel Required",
                            "You need to be in a voice channel to use this command!",
                            discord.Color.yellow()
                        )
                        await message.channel.send(embed=embed)
                    else:
                        await message.channel.send("❗ You need to be in a voice channel to use this command!")
                    return

                # Check if there are attachments
                if not message.attachments:
                    if can_embed:
                        embed = self.music_ui.create_embed(
                            f"{self.music_ui.emoji['warning']} No Files Attached",
                            "Please attach audio files when mentioning the bot.",
                            discord.Color.yellow()
                        )
                        await message.channel.send(embed=embed)
                    else:
                        await message.channel.send("❗ Please attach audio files when mentioning the bot.")
                    return

                # Define supported audio formats
                SUPPORTED_FORMATS = ('.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac', '.mp4')
                audio_attachments = [
                    att for att in message.attachments 
                    if any(att.filename.lower().endswith(fmt) for fmt in SUPPORTED_FORMATS)
                ]

                if not audio_attachments:
                    if can_embed:
                        embed = self.music_ui.create_embed(
                            f"{self.music_ui.emoji['error']} Invalid File Type",
                            f"Please provide audio files in one of these formats: {', '.join(SUPPORTED_FORMATS)}",
                            discord.Color.red()
                        )
                        await message.channel.send(embed=embed)
                    else:
                        await message.channel.send(f"❌ Please provide supported audio files: {', '.join(SUPPORTED_FORMATS)}")
                    return

                # Get guild state
                guild_state = await self.music_state.get_guild_state(message.guild.id)
                
                # Calculate total size of all audio files
                total_size = sum(att.size for att in audio_attachments)
                
                # Check queue size limits
                if not self.track_manager.can_add_to_queue(guild_state.queue, total_size):
                    current_size_mb = self.track_manager.get_queue_size(guild_state.queue) / (1024 * 1024)
                    if can_embed:
                        embed = self.music_ui.create_embed(
                            f"{self.music_ui.emoji['warning']} Queue Full",
                            f"Queue size limit reached! Current size: {current_size_mb:.1f}MB",
                            discord.Color.yellow()
                        )
                        await message.channel.send(embed=embed)
                    else:
                        await message.channel.send(f"❗ Queue size limit reached! Current size: {current_size_mb:.1f}MB")
                    return

                # Check voice channel permissions
                if not message.author.voice.channel.permissions_for(message.guild.me).connect:
                    if can_embed:
                        embed = self.music_ui.create_embed(
                            f"{self.music_ui.emoji['error']} Missing Permissions",
                            "I don't have permission to join the voice channel!",
                            discord.Color.red()
                        )
                        await message.channel.send(embed=embed)
                    else:
                        await message.channel.send("❌ I don't have permission to join the voice channel!")
                    return

                if not message.author.voice.channel.permissions_for(message.guild.me).speak:
                    if can_embed:
                        embed = self.music_ui.create_embed(
                            f"{self.music_ui.emoji['error']} Missing Permissions",
                            "I don't have permission to speak in the voice channel!",
                            discord.Color.red()
                        )
                        await message.channel.send(embed=embed)
                    else:
                        await message.channel.send("❌ I don't have permission to speak in the voice channel!")
                    return

                # Add all tracks to queue
                added_tracks = []
                skipped_tracks = []
                for attachment in audio_attachments:
                    try:
                        # Create track
                        track = AudioTrack(
                            attachment.url,
                            attachment.filename,
                            message.author.display_name,
                            attachment.size
                        )
                        
                        # Add to queue
                        guild_state.queue.append(track)
                        added_tracks.append(attachment.filename)
                    except Exception as e:
                        logging.error(f"Error adding track {attachment.filename}: {e}")
                        skipped_tracks.append(attachment.filename)

                # Prepare and send status message
                if can_embed:
                    # Prepare status message for embed
                    status_message = []
                    if added_tracks:
                        status_message.append(f"✅ Added {len(added_tracks)} tracks to queue:")
                        for i, track in enumerate(added_tracks, 1):
                            status_message.append(f"{i}. {track}")

                    if skipped_tracks:
                        status_message.append(f"\n❌ Failed to add {len(skipped_tracks)} tracks:")
                        for track in skipped_tracks:
                            status_message.append(f"• {track}")

                    current_size_mb = self.track_manager.get_queue_size(guild_state.queue) / (1024 * 1024)
                    max_size_mb = self.bot.config['max_queue_size_mb']
                    status_message.append(f"\n{self.music_ui.emoji['cd']} Queue Size: {current_size_mb:.1f}MB / {max_size_mb}MB")

                    embed = self.music_ui.create_embed(
                        f"{self.music_ui.emoji['success']} Batch Upload Complete",
                        "\n".join(status_message),
                        discord.Color.green()
                    )
                    await message.channel.send(embed=embed)
                else:
                    # Prepare simple text status message
                    status_message = []
                    if added_tracks:
                        status_message.append(f"✅ Added {len(added_tracks)} tracks to queue")
                    if skipped_tracks:
                        status_message.append(f"❌ Failed to add {len(skipped_tracks)} tracks")
                    
                    current_size_mb = self.track_manager.get_queue_size(guild_state.queue) / (1024 * 1024)
                    status_message.append(f"📀 Queue Size: {current_size_mb:.1f}MB")
                    
                    await message.channel.send("\n".join(status_message))

                # Connect to voice channel if not already connected
                if not message.guild.voice_client and added_tracks:
                    try:
                        await message.author.voice.channel.connect()
                        # Check autoplay setting before starting playback
                        if self.db.get_autoplay_setting(message.guild.id):
                            await self.music_playback.play_next(message.guild)
                    except discord.Forbidden:
                        logging.error("Failed to connect to voice channel - Missing permissions")
                        if can_embed:
                            embed = self.music_ui.create_embed(
                                f"{self.music_ui.emoji['error']} Connection Error",
                                "Failed to connect to voice channel due to missing permissions!",
                                discord.Color.red()
                            )
                            await message.channel.send(embed=embed)
                        else:
                            await message.channel.send("❌ Failed to connect to voice channel due to missing permissions!")
                    except Exception as e:
                        logging.error(f"Error connecting to voice channel: {e}")
                        if can_embed:
                            embed = self.music_ui.create_embed(
                                f"{self.music_ui.emoji['error']} Connection Error",
                                "Failed to connect to voice channel!",
                                discord.Color.red()
                            )
                            await message.channel.send(embed=embed)
                        else:
                            await message.channel.send("❌ Failed to connect to voice channel!")
                        return
                # If already connected and nothing is playing, start playback if autoplay is enabled
                elif (message.guild.voice_client and 
                    not message.guild.voice_client.is_playing() and 
                    self.db.get_autoplay_setting(message.guild.id) and 
                    added_tracks):
                    await self.music_playback.play_next(message.guild)

            except discord.Forbidden as e:
                logging.error(f"Permission error in on_message handler: {e}")
                # Just log the error and return if we don't have permissions
                return
            except Exception as e:
                logging.error(f"Error in batch upload handler: {e}")
                try:
                    if can_embed:
                        embed = self.music_ui.create_embed(
                            f"{self.music_ui.emoji['error']} Error",
                            "An unexpected error occurred while processing your request.",
                            discord.Color.red()
                        )
                        await message.channel.send(embed=embed)
                    else:
                        await message.channel.send("❌ An unexpected error occurred while processing your request.")
                except:
                    logging.error("Failed to send error message")
                
                # Attempt to clean up if there was an error
                try:
                    guild_state = await self.music_state.get_guild_state(message.guild.id)
                    if guild_state.current_track:
                        guild_state.current_track.cleanup()
                except Exception as cleanup_error:
                    logging.error(f"Error during cleanup after batch upload error: {cleanup_error}")