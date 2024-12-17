import discord
from discord.ext import commands
from discord import app_commands
from datetime import timedelta
import time
import logging
import asyncio
from utils.track_manager import AudioTrack, TrackManager
from utils.database_manager import DatabaseManager
from utils.permission_checks import check_permissions, admin_only

class Music(commands.Cog):
    def __init__(self, bot):
        self.alone_since = {}
        self.bot = bot
        self.guild_states = {}  # Store states for each guild
        self.track_manager = TrackManager(bot.config)
        self.ffmpeg_path = './ffmpeg'
        self.db = DatabaseManager()  # Initialize database manager
        
        # Rate limiting and cleanup
        self.rate_limits = {}
        self.cleanup_task = bot.loop.create_task(self.periodic_cleanup())

        # Emojis for various states and actions
        self.emoji = {
            'play': '▶️',
            'pause': '⏸️',
            'resume': '⏯️',
            'stop': '⏹️',
            'skip': '⏭️',
            'queue': '📜',
            'music': '🎵',
            'warning': '⚠️',
            'error': '❌',
            'success': '✅',
            'time': '⏰',
            'volume': '🔊',
            'low_volume': '🔈',
            'mute': '🔇',
            'disconnect': '👋',
            'loading': '⏳',
            'microphone': '🎙️',
            'cd': '💿',
            'settings' : '⚙️',
            'user': '👤',
            'role': '👥' 
        }

    class GuildState:
        def __init__(self):
            self.queue = []
            self.current_track = None
            self.volume = 100
            self.is_seeking = False
            self.last_activity = time.time()
    
    async def get_guild_state(self, guild_id: int) -> GuildState:
        """Get or create guild state"""
        if guild_id not in self.guild_states:
            self.guild_states[guild_id] = self.GuildState()
        self.guild_states[guild_id].last_activity = time.time()
        return self.guild_states[guild_id]

    async def periodic_cleanup(self):
        """Cleanup inactive guilds, temporary files, and stale alone timers"""
        while True:
            try:
                current_time = time.time()
                inactive_guilds = []
                cleaned_files = 0
                error_count = 0

                # 1. Clean up alone timers (5 minute threshold)
                for guild_id in list(self.alone_since.keys()):
                    try:
                        if current_time - self.alone_since[guild_id] > 300:  # 5 minutes
                            guild = self.bot.get_guild(guild_id)
                            if guild and guild.voice_client:
                                voice_channel = guild.voice_client.channel
                                if len(voice_channel.members) == 1:  # Still alone
                                    await guild.voice_client.disconnect()
                                    logging.info(f"Disconnected from guild {guild_id} after being alone for 5 minutes")
                                
                            self.alone_since.pop(guild_id, None)
                    except Exception as e:
                        logging.error(f"Error cleaning up alone timer for guild {guild_id}: {e}")
                        error_count += 1

                # 2. Clean up inactive guilds (1 hour threshold)
                for guild_id, state in list(self.guild_states.items()):
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
                            del self.guild_states[guild_id]
                            inactive_guilds.append(guild_id)
                            
                            logging.info(f"Cleaned up inactive guild {guild_id}")
                    except Exception as e:
                        logging.error(f"Error cleaning up inactive guild {guild_id}: {e}")
                        error_count += 1

                # 3. Clean up rate limits (60 second threshold)
                try:
                    self.rate_limits = {
                        guild_id: time for guild_id, time in self.rate_limits.items()
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

    async def check_rate_limit(self, guild_id: int) -> bool:
        """Basic rate limiting per guild"""
        current_time = time.time()
        if guild_id in self.rate_limits:
            if current_time - self.rate_limits[guild_id] < 2:  # 2 second cooldown
                return False
        self.rate_limits[guild_id] = current_time
        return True

    @commands.Cog.listener()
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
                    self.alone_since[before.channel.guild.id] = time.time()
                    
                    # Wait 5 minutes before checking again
                    await asyncio.sleep(300)  # 300 seconds = 5 minutes
                    
                    # Check if we're still alone after 5 minutes
                    current_voice_client = before.channel.guild.voice_client
                    if (current_voice_client and 
                        current_voice_client.channel == before.channel and 
                        len(before.channel.members) == 1):
                        
                        await current_voice_client.disconnect()
                        guild_state = await self.get_guild_state(before.channel.guild.id)
                        if guild_state.current_track:
                            guild_state.current_track.cleanup()
                        for track in guild_state.queue:
                            track.cleanup()
                        guild_state.queue.clear()
                        
                        # Clean up the alone_since entry
                        self.alone_since.pop(before.channel.guild.id, None)
                else:
                    # If we're not alone anymore, remove the alone_since entry
                    self.alone_since.pop(before.channel.guild.id, None)

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

    def get_pcm_audio(self, track, start_time=0):
        """Get PCM audio source for playback"""
        track.last_accessed = time.time()
        
        # Ensure start_time is within valid range
        start_time = max(0, min(start_time, track.duration))
        
        # Format timestamp properly for FFmpeg
        timestamp = str(timedelta(seconds=int(start_time)))
        options = f'-ss {timestamp}'
        
        try:
            audio_source = discord.FFmpegPCMAudio(
                track.downloaded_path,
                before_options=options,
                executable=self.ffmpeg_path,
                options='-vn -b:a 128k'  # Add explicit audio options
            )
            return discord.PCMVolumeTransformer(audio_source, volume=track.volume / 100)
        except Exception as e:
            logging.error(f"Error creating audio source: {e}")
            raise

    async def play_next(self, guild, force_play=False):
        """Play the next track in the queue"""
        guild_state = await self.get_guild_state(guild.id)
        if guild_state.is_seeking:
            return  # Don't proceed if we're in the middle of a seek operation
                
        try:
            # Clean up current track if exists
            if guild_state.current_track and not guild_state.is_seeking:
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
                
                # Download if not already downloaded
                if not guild_state.current_track.downloaded_path:
                    await self.track_manager.ensure_temp_folder()
                    await guild_state.current_track.download(self.bot.config['temp_folder'])
                    
                # Update last activity time
                guild_state.last_activity = time.time()
                
            except Exception as e:
                logging.error(f"Failed to prepare next track in guild {guild.id}: {e}")
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
                audio_source = self.get_pcm_audio(
                    guild_state.current_track, 
                    guild_state.current_track.position
                )
                
                # Set volume from guild state
                audio_source.volume = guild_state.volume / 100
                
            except Exception as e:
                logging.error(f"Error creating audio source in guild {guild.id}: {e}")
                guild_state.current_track = None
                # Try to play next track in queue if this one fails
                await self.play_next(guild, force_play)
                return

            # Define after-playing callback
            def after_playing(error):
                if error:
                    logging.error(f'Player error in guild {guild.id}: {error}')
                
                # Reset alone timer if it exists
                self.alone_since.pop(guild.id, None)
                
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
                self.alone_since.pop(guild.id, None)
                
            except Exception as e:
                logging.error(f"Error starting playback in guild {guild.id}: {e}")
                guild_state.current_track = None
                # Try to play next track in queue if this one fails
                await self.play_next(guild, force_play)
                return

        except Exception as e:
            logging.error(f"Unexpected error in play_next for guild {guild.id}: {e}")
            # Clean up if there was an error
            try:
                if guild_state.current_track:
                    guild_state.current_track.cleanup()
                    guild_state.current_track = None
            except Exception as cleanup_error:
                logging.error(f"Error during cleanup after playback failure in guild {guild.id}: {cleanup_error}")

    @commands.Cog.listener()
    @check_permissions()
    async def on_message(self, message):
        if message.author.bot:
            return

        if self.bot.user.mentioned_in(message) and message.attachments:
            # Check if user is in voice channel
            if not message.author.voice:
                embed = self.create_embed(
                    f"{self.emoji['warning']} Voice Channel Required",
                    "You need to be in a voice channel to use this command!",
                    discord.Color.yellow()
                )
                await message.channel.send(embed=embed)
                return

            # Check if user is blacklisted
            if self.db.is_user_blacklisted(message.guild.id, message.author.id):
                embed = self.create_embed(
                    f"{self.emoji['error']} Access Denied",
                    "You are blacklisted from using this bot.",
                    discord.Color.red()
                )
                await message.channel.send(embed=embed)
                return

            # Check role whitelist
            whitelisted_roles = self.db.get_whitelisted_roles(message.guild.id)
            if whitelisted_roles:
                user_roles = [role.id for role in message.author.roles]
                if not any(role_id in user_roles for role_id in whitelisted_roles):
                    embed = self.create_embed(
                        f"{self.emoji['error']} Access Denied",
                        "You don't have the required role to use this bot.",
                        discord.Color.red()
                    )
                    await message.channel.send(embed=embed)
                    return

            # Check file type
            attachment = message.attachments[0]
            if not attachment.filename.endswith('.mp3'):
                embed = self.create_embed(
                    f"{self.emoji['error']} Invalid File Type",
                    "Please provide an MP3 file!",
                    discord.Color.red()
                )
                await message.channel.send(embed=embed)
                return

            # Check queue size limits
            guild_state = await self.get_guild_state(message.guild.id)
            if not self.track_manager.can_add_to_queue(guild_state.queue, attachment.size):
                current_size_mb = self.track_manager.get_queue_size(guild_state.queue) / (1024 * 1024)
                embed = self.create_embed(
                    f"{self.emoji['warning']} Queue Full",
                    f"Queue size limit reached! Current size: {current_size_mb:.1f}MB",
                    discord.Color.yellow()
                )
                await message.channel.send(embed=embed)
                return

            # Add track to queue
            track = AudioTrack(
                attachment.url,
                attachment.filename,
                message.author.display_name,
                attachment.size
            )
            
            guild_state.queue.append(track)
            current_size_mb = self.track_manager.get_queue_size(guild_state.queue) / (1024 * 1024)
            max_size_mb = self.bot.config['max_queue_size_mb']
            
            # Send confirmation message
            embed = self.create_embed(
                f"{self.emoji['success']} Track Added",
                f"{self.emoji['music']} **Track:** {attachment.filename}\n"
                f"{self.emoji['microphone']} **Requested by:** {message.author.display_name}\n"
                f"{self.emoji['cd']} **Queue Size:** {current_size_mb:.1f}MB / {max_size_mb}MB",
                discord.Color.green()
            )
            await message.channel.send(embed=embed)

            # Connect to voice channel if not already connected
            if not message.guild.voice_client:
                try:
                    await message.author.voice.channel.connect()
                    # Check autoplay setting before starting playback
                    if self.db.get_autoplay_setting(message.guild.id):
                        await self.play_next(message.guild)
                except Exception as e:
                    logging.error(f"Error connecting to voice channel: {e}")
                    embed = self.create_embed(
                        f"{self.emoji['error']} Connection Error",
                        "Failed to connect to voice channel!",
                        discord.Color.red()
                    )
                    await message.channel.send(embed=embed)
                    return
            # If already connected and nothing is playing, start playback if autoplay is enabled
            elif not message.guild.voice_client.is_playing() and self.db.get_autoplay_setting(message.guild.id):
                await self.play_next(message.guild)
        
    @app_commands.command(name="blacklist", description="Add or remove a user from the blacklist")
    @admin_only()
    async def blacklist(self, interaction: discord.Interaction, action: str, user: discord.Member):
        try:
            if action.lower() not in ['add', 'remove']:
                embed = self.create_embed(
                    f"{self.emoji['warning']} Invalid Action",
                    "Action must be either 'add' or 'remove'.",
                    discord.Color.yellow()
                )
                await interaction.response.send_message(embed=embed)
                return

            if action.lower() == 'add':
                self.db.add_to_blacklist(interaction.guild_id, user.id)
                action_text = "added to"
            else:
                self.db.remove_from_blacklist(interaction.guild_id, user.id)
                action_text = "removed from"

            embed = self.create_embed(
                f"{self.emoji['success']} Blacklist Updated",
                f"{user.mention} has been {action_text} the blacklist.",
                discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error managing blacklist: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Error",
                "Failed to update blacklist.",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="role_config", description="Add or remove a role from the whitelist")
    @admin_only()
    async def role_config(self, interaction: discord.Interaction, action: str, role: discord.Role):
        try:
            if action.lower() not in ['add', 'remove']:
                embed = self.create_embed(
                    f"{self.emoji['warning']} Invalid Action",
                    "Action must be either 'add' or 'remove'.",
                    discord.Color.yellow()
                )
                await interaction.response.send_message(embed=embed)
                return

            if action.lower() == 'add':
                self.db.add_to_role_whitelist(interaction.guild_id, role.id)
                action_text = "added to"
            else:
                self.db.remove_from_role_whitelist(interaction.guild_id, role.id)
                action_text = "removed from"

            embed = self.create_embed(
                f"{self.emoji['success']} Role Whitelist Updated",
                f"{role.mention} has been {action_text} the role whitelist.",
                discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error managing role whitelist: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Error",
                "Failed to update role whitelist.",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="autodisconnect", description="Enable or disable auto-disconnect when queue is empty")
    @admin_only()
    async def autodisconnect(self, interaction: discord.Interaction, enabled: bool):
        try:
            self.db.set_autodisconnect_setting(interaction.guild_id, enabled)
            status = "enabled" if enabled else "disabled"
            embed = self.create_embed(
                f"{self.emoji['success']} Auto-Disconnect Updated",
                f"Auto-disconnect when queue is empty has been {status}.",
                discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error setting autodisconnect: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Error",
                "Failed to update auto-disconnect setting.",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="autoplay", description="Enable or disable autoplay")
    @admin_only()
    async def autoplay(self, interaction: discord.Interaction, enabled: bool):
        try:
            self.db.set_autoplay_setting(interaction.guild_id, enabled)
            status = "enabled" if enabled else "disabled"
            embed = self.create_embed(
                f"{self.emoji['success']} Autoplay Updated",
                f"Autoplay has been {status}.",
                discord.Color.green()
            )
            self.alone_since.pop(interaction.guild_id, None)
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error setting autoplay: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Error",
                "Failed to update autoplay setting.",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="play", description="Play the current loaded MP3")
    @check_permissions()
    async def play(self, interaction: discord.Interaction):
        # Defer response immediately
        await interaction.response.defer()
        
        try:
            # Check if user is in a voice channel
            if not interaction.user.voice:
                embed = self.create_embed(
                    f"{self.emoji['warning']} Voice Channel Required",
                    "You need to be in a voice channel to use this command!",
                    discord.Color.yellow()
                )
                await interaction.followup.send(embed=embed)
                return

            # Get guild state and check queue
            guild_state = await self.get_guild_state(interaction.guild_id)
            if not guild_state.queue:
                embed = self.create_embed(
                    f"{self.emoji['warning']} Empty Queue",
                    "No songs in queue! Add some MP3 files first.",
                    discord.Color.yellow()
                )
                await interaction.followup.send(embed=embed)
                return

            # Get or create voice client
            voice_client = interaction.guild.voice_client
            if not voice_client:
                try:
                    voice_client = await interaction.user.voice.channel.connect()
                except Exception as e:
                    logging.error(f"Failed to connect to voice channel: {e}")
                    embed = self.create_embed(
                        f"{self.emoji['error']} Connection Failed",
                        "Failed to connect to voice channel!",
                        discord.Color.red()
                    )
                    await interaction.followup.send(embed=embed)
                    return

            # Check if already playing
            if voice_client.is_playing():
                embed = self.create_embed(
                    f"{self.emoji['warning']} Already Playing",
                    "A track is already playing! Use /skip to play the next track.",
                    discord.Color.yellow()
                )
                await interaction.followup.send(embed=embed)
                return

            try:
                # Attempt to play next track with force_play=True to override autoplay setting
                await self.play_next(interaction.guild, force_play=True)
                
                # Check if track started playing successfully
                if guild_state.current_track:
                    # Calculate progress bar
                    progress = int((guild_state.current_track.position / guild_state.current_track.duration) * 20)
                    progress_bar = f"{'▰' * progress}{'▱' * (20 - progress)}"
                    
                    # Create success embed with detailed information
                    embed = self.create_embed(
                        f"{self.emoji['play']} Now Playing",
                        f"{self.emoji['music']} **Track:** {guild_state.current_track.filename}\n"
                        f"{self.emoji['microphone']} **Requested by:** {guild_state.current_track.requester}\n"
                        f"{self.emoji['time']} **Duration:** {self.format_duration(int(guild_state.current_track.duration))}\n"
                        f"**Progress:** {progress_bar}",
                        discord.Color.green()
                    )
                    
                    # Add queue information if there are more tracks
                    if guild_state.queue:
                        next_track = guild_state.queue[0]
                        embed.add_field(
                            name=f"{self.emoji['queue']} Up Next",
                            value=f"{self.emoji['music']} {next_track.filename}\n"
                                f"{self.emoji['microphone']} Requested by: {next_track.requester}",
                            inline=False
                        )
                    
                    await interaction.followup.send(embed=embed)
                else:
                    embed = self.create_embed(
                        f"{self.emoji['error']} Playback Failed",
                        "Failed to start playback. Please try again or check the file.",
                        discord.Color.red()
                    )
                    await interaction.followup.send(embed=embed)

            except Exception as e:
                logging.error(f"Error playing track: {e}")
                embed = self.create_embed(
                    f"{self.emoji['error']} Playback Error",
                    f"An error occurred while trying to play: {str(e)}",
                    discord.Color.red()
                )
                await interaction.followup.send(embed=embed)
                
                # Attempt to clean up if there was an error
                try:
                    if guild_state.current_track:
                        guild_state.current_track.cleanup()
                        guild_state.current_track = None
                except Exception as cleanup_error:
                    logging.error(f"Error during cleanup after playback failure: {cleanup_error}")

        except Exception as e:
            logging.error(f"Unexpected error in play command: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Error",
                "An unexpected error occurred while processing the command.",
                discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
            
    @app_commands.command(name="pause", description="Pause the current song")
    @check_permissions()
    async def pause(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_playing():
            embed = self.create_embed(
                f"{self.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            voice_client.pause()
            guild_state = await self.get_guild_state(interaction.guild_id)
            embed = self.create_embed(
                f"{self.emoji['pause']} Paused",
                f"Paused: **{guild_state.current_track.filename}**\n"
                f"Use `/resume` to continue playback",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error pausing playback: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Pause Error",
                "Failed to pause the music!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="resume", description="Resume the current song")
    @check_permissions()
    async def resume(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_paused():
            embed = self.create_embed(
                f"{self.emoji['warning']} Not Paused",
                "Nothing is currently paused!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            voice_client.resume()
            guild_state = await self.get_guild_state(interaction.guild_id)
            embed = self.create_embed(
                f"{self.emoji['resume']} Resumed",
                f"Resumed: **{guild_state.current_track.filename}**",
                discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error resuming playback: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Resume Error",
                "Failed to resume the music!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="queue", description="Show the current queue")
    @check_permissions()
    async def queue(self, interaction: discord.Interaction):
        guild_state = await self.get_guild_state(interaction.guild_id)
        if not guild_state.queue:
            embed = self.create_embed(
                f"{self.emoji['queue']} Queue Empty",
                "No tracks in queue! Add some MP3 files to get started.",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            current_size_mb = self.track_manager.get_queue_size(guild_state.queue) / (1024 * 1024)
            max_size_mb = self.bot.config['max_queue_size_mb']
            
            queue_list = "\n".join(
                f"`{idx + 1}.` {self.emoji['music']} **{track.filename}**\n"
                f"┗ {self.emoji['microphone']} Requested by: {track.requester}"
                for idx, track in enumerate(guild_state.queue)
            )
            
            embed = self.create_embed(
                f"{self.emoji['queue']} Current Queue",
                f"{queue_list}\n\n"
                f"{self.emoji['cd']} **Queue Size:** {current_size_mb:.1f}MB / {max_size_mb}MB\n"
                f"{self.emoji['music']} **Tracks in Queue:** {len(guild_state.queue)}",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error displaying queue: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Queue Error",
                "Failed to display the queue!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="playing", description="Show what's currently playing")
    @check_permissions()
    async def playing(self, interaction: discord.Interaction):
        guild_state = await self.get_guild_state(interaction.guild_id)
        if not guild_state.current_track:
            embed = self.create_embed(
                f"{self.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            current_position = self.format_duration(int(guild_state.current_track.position))
            total_duration = self.format_duration(int(guild_state.current_track.duration))
            progress = int((guild_state.current_track.position / guild_state.current_track.duration) * 20)
            
            progress_bar = f"{'▰' * progress}{'▱' * (20 - progress)}"
            
            embed = self.create_embed(
                f"{self.emoji['play']} Now Playing",
                f"{self.emoji['music']} **Track:** {guild_state.current_track.filename}\n"
                f"{self.emoji['microphone']} **Requested by:** {guild_state.current_track.requester}\n"
                f"{self.emoji['time']} **Time:** `{current_position} / {total_duration}`\n"
                f"**Progress:** {progress_bar}",
                discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error displaying current track: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Display Error",
                "Failed to display current track info!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="volume", description="Set the volume (0-120)")
    @check_permissions()
    async def volume(self, interaction: discord.Interaction, volume: int):
        if volume < 0 or volume > 120:
            embed = self.create_embed(
                f"{self.emoji['warning']} Invalid Volume",
                "Volume must be between 0 and 120!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            guild_state = await self.get_guild_state(interaction.guild_id)
            guild_state.volume = volume
            
            voice_client = interaction.guild.voice_client
            if voice_client and voice_client.source:
                voice_client.source.volume = volume / 100
            
            # Choose appropriate volume emoji
            volume_emoji = self.emoji['volume']
            if volume == 0:
                volume_emoji = self.emoji['mute']
            elif volume < 50:
                volume_emoji = self.emoji['low_volume']
            
            embed = self.create_embed(
                f"{volume_emoji} Volume Updated",
                f"Set volume to **{volume}%**",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error setting volume: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Volume Error",
                "Failed to set volume!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="forward", description="Skip forward by specified seconds")
    @check_permissions()
    async def forward(self, interaction: discord.Interaction, seconds: int):
        guild_state = await self.get_guild_state(interaction.guild_id)
        if not guild_state.current_track:
            embed = self.create_embed(
                f"{self.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        if seconds <= 0:
            embed = self.create_embed(
                f"{self.emoji['warning']} Invalid Time",
                "Please specify a positive number of seconds!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            current_pos = guild_state.current_track.position
            new_position = min(current_pos + seconds, guild_state.current_track.duration - 1)
            
            voice_client = interaction.guild.voice_client
            if not voice_client or not voice_client.is_connected():
                embed = self.create_embed(
                    f"{self.emoji['error']} Not Connected",
                    "Bot is not connected to a voice channel!",
                    discord.Color.red()
                )
                await interaction.response.send_message(embed=embed)
                return

            guild_state.is_seeking = True
            guild_state.current_track.position = new_position
            
            if voice_client.is_playing():
                voice_client.stop()
            
            await asyncio.sleep(0.5)
            
            audio_source = self.get_pcm_audio(guild_state.current_track, new_position)
            
            def after_seeking(error):
                guild_state.is_seeking = False
                if error:
                    logging.error(f'Seeking error: {error}')
            
            voice_client.play(audio_source, after=after_seeking)
            
            # Calculate actual seconds moved forward
            actual_skip = new_position - current_pos
            
            # Calculate progress bar
            progress = int((new_position / guild_state.current_track.duration) * 20)
            progress_bar = f"{'▰' * progress}{'▱' * (20 - progress)}"
            
            embed = self.create_embed(
                f"{self.emoji['time']} Skipped Forward",
                f"{self.emoji['music']} **Track:** {guild_state.current_track.filename}\n"
                f"{self.emoji['time']} **Skipped:** {actual_skip} seconds forward\n"
                f"**New Position:** {self.format_duration(new_position)} / {self.format_duration(int(guild_state.current_track.duration))}\n"
                f"**Progress:** {progress_bar}",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            guild_state.is_seeking = False
            logging.error(f"Error during forward seek: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Forward Error",
                "An error occurred while seeking forward!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="backward", description="Skip backward by specified seconds")
    @check_permissions()
    async def backward(self, interaction: discord.Interaction, seconds: int):
        guild_state = await self.get_guild_state(interaction.guild_id)
        if not guild_state.current_track:
            embed = self.create_embed(
                f"{self.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        if seconds <= 0:
            embed = self.create_embed(
                f"{self.emoji['warning']} Invalid Time",
                "Please specify a positive number of seconds!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            current_pos = guild_state.current_track.position
            new_position = max(0, current_pos - seconds)
            
            voice_client = interaction.guild.voice_client
            if not voice_client or not voice_client.is_connected():
                embed = self.create_embed(
                    f"{self.emoji['error']} Not Connected",
                    "Bot is not connected to a voice channel!",
                    discord.Color.red()
                )
                await interaction.response.send_message(embed=embed)
                return

            guild_state.is_seeking = True
            guild_state.current_track.position = new_position
            
            if voice_client.is_playing():
                voice_client.stop()
            
            await asyncio.sleep(0.5)
            
            audio_source = self.get_pcm_audio(guild_state.current_track, new_position)
            
            def after_seeking(error):
                guild_state.is_seeking = False
                if error:
                    logging.error(f'Seeking error: {error}')
            
            voice_client.play(audio_source, after=after_seeking)
            
            # Calculate actual seconds moved backward
            actual_skip = current_pos - new_position
            
            # Calculate progress bar
            progress = int((new_position / guild_state.current_track.duration) * 20)
            progress_bar = f"{'▰' * progress}{'▱' * (20 - progress)}"
            
            embed = self.create_embed(
                f"{self.emoji['time']} Skipped Backward",
                f"{self.emoji['music']} **Track:** {guild_state.current_track.filename}\n"
                f"{self.emoji['time']} **Skipped:** {actual_skip} seconds backward\n"
                f"**New Position:** {self.format_duration(new_position)} / {self.format_duration(int(guild_state.current_track.duration))}\n"
                f"**Progress:** {progress_bar}",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            guild_state.is_seeking = False
            logging.error(f"Error during backward seek: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Backward Error",
                "An error occurred while seeking backward!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="timestamp", description="Set the current song position (hh:mm:ss)")
    @check_permissions()
    async def timestamp(self, interaction: discord.Interaction, hours: int, minutes: int, seconds: int):
        guild_state = await self.get_guild_state(interaction.guild_id)
        if not guild_state.current_track:
            embed = self.create_embed(
                f"{self.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        if hours < 0 or minutes < 0 or seconds < 0 or seconds >= 60 or minutes >= 60:
            embed = self.create_embed(
                f"{self.emoji['warning']} Invalid Time",
                "Invalid timestamp! Format: hours >= 0, 0 <= minutes < 60, 0 <= seconds < 60",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        new_position = (hours * 3600) + (minutes * 60) + seconds
        
        if new_position >= guild_state.current_track.duration:
            total_duration = self.format_duration(int(guild_state.current_track.duration))
            embed = self.create_embed(
                f"{self.emoji['warning']} Invalid Position",
                f"Cannot seek beyond the end of the track! Maximum duration is {total_duration}",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            embed = self.create_embed(
                f"{self.emoji['error']} Not Connected",
                "Bot is not connected to a voice channel!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            guild_state.is_seeking = True
            guild_state.current_track.position = new_position
            
            if voice_client.is_playing():
                voice_client.stop()
            
            await asyncio.sleep(0.5)
            
            audio_source = self.get_pcm_audio(guild_state.current_track, guild_state.current_track.position)
            
            def after_seeking(error):
                guild_state.is_seeking = False
                if error:
                    logging.error(f'Seeking error: {error}')
            
            voice_client.play(audio_source, after=after_seeking)
            
            new_timestamp = self.format_duration(new_position)
            total_duration = self.format_duration(int(guild_state.current_track.duration))
            
            # Calculate progress bar
            progress = int((new_position / guild_state.current_track.duration) * 20)
            progress_bar = f"{'▰' * progress}{'▱' * (20 - progress)}"
            
            embed = self.create_embed(
                f"{self.emoji['time']} Position Updated",
                f"{self.emoji['music']} **Track:** {guild_state.current_track.filename}\n"
                f"{self.emoji['time']} **New Position:** {new_timestamp} / {total_duration}\n"
                f"**Progress:** {progress_bar}",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            guild_state.is_seeking = False
            logging.error(f"Error during timestamp seek: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Seeking Error",
                "An error occurred while seeking!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="skip", description="Skip the current song")
    @check_permissions()
    async def skip(self, interaction: discord.Interaction):
        guild_state = await self.get_guild_state(interaction.guild_id)
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_playing():
            embed = self.create_embed(
                f"{self.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            skipped_track = guild_state.current_track.filename
            voice_client.stop()  # This will trigger play_next via the after callback
            
            # Show next track info if available
            next_track_info = (f"\n{self.emoji['play']} **Up next:** {guild_state.queue[0].filename}" 
                             if guild_state.queue else "\nQueue is now empty")
            
            embed = self.create_embed(
                f"{self.emoji['skip']} Track Skipped",
                f"{self.emoji['music']} **Skipped:** {skipped_track}{next_track_info}",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error skipping track: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Skip Error",
                "Failed to skip the track!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="clear", description="Clear the queue")
    @check_permissions()
    async def clear(self, interaction: discord.Interaction):
        guild_state = await self.get_guild_state(interaction.guild_id)
        try:
            queue_length = len(guild_state.queue)
            # Clean up all queued tracks
            for track in guild_state.queue:
                track.cleanup()
            
            guild_state.queue.clear()
            
            voice_client = interaction.guild.voice_client
            if voice_client and voice_client.is_playing():
                voice_client.stop()
            
            embed = self.create_embed(
                f"{self.emoji['success']} Queue Cleared",
                f"{self.emoji['music']} Cleared {queue_length} tracks from the queue\n"
                f"{self.emoji['stop']} Playback stopped",
                discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error clearing queue: {e}")
            embed = self.create_embed(
                f"{self.emoji['error']} Clear Error",
                "Failed to clear the queue!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="disconnect", description="Disconnect the bot from voice")
    @check_permissions()
    async def disconnect(self, interaction: discord.Interaction):
        guild_state = await self.get_guild_state(interaction.guild_id)
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_connected():
            try:
                if guild_state.current_track:
                    guild_state.current_track.cleanup()
                    guild_state.current_track = None
                
                for track in guild_state.queue:
                    track.cleanup()
                queue_length = len(guild_state.queue)
                guild_state.queue.clear()
                
                await voice_client.disconnect()
                
                embed = self.create_embed(
                    f"{self.emoji['disconnect']} Disconnected",
                    f"{self.emoji['success']} Successfully disconnected from voice channel\n"
                    f"{self.emoji['queue']} Cleared {queue_length} tracks from queue\n"
                    f"{self.emoji['stop']} All resources cleaned up",
                    discord.Color.blue()
                )
                await interaction.response.send_message(embed=embed)
            except Exception as e:
                logging.error(f"Error disconnecting: {e}")
                embed = self.create_embed(
                    f"{self.emoji['error']} Disconnect Error",
                    "Failed to disconnect properly!",
                    discord.Color.red()
                )
                await interaction.response.send_message(embed=embed)
        else:
            embed = self.create_embed(
                f"{self.emoji['warning']} Not Connected",
                "Not connected to a voice channel!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="help", description="Show all available commands")
    async def help(self, interaction: discord.Interaction):
        commands_list = [
            f"{self.emoji['play']} **/play** - Play the current loaded MP3",
            f"{self.emoji['pause']} **/pause** - Pause the current song",
            f"{self.emoji['resume']} **/resume** - Resume the current song",
            f"{self.emoji['queue']} **/queue** - Show the current queue",
            f"{self.emoji['music']} **/playing** - Show what's currently playing",
            f"{self.emoji['stop']} **/clear** - Clear the queue",
            f"{self.emoji['volume']} **/volume <0-120>** - Set the volume",
            f"{self.emoji['skip']} **/skip** - Skip the current song",
            f"{self.emoji['time']} **/forward <seconds>** - Skip forward by specified seconds",
            f"{self.emoji['time']} **/backward <seconds>** - Skip backward by specified seconds",
            f"{self.emoji['time']} **/timestamp <hours> <minutes> <seconds>** - Set track position",
            f"{self.emoji['disconnect']} **/disconnect** - Disconnect the bot\n",  # Add newline here
            f"{self.emoji['settings']} **/autoplay <true/false>** - Enable or disable autoplay",
            f"{self.emoji['settings']} **/autodisconnect <true/false>** - Enable/disable auto-disconnect when queue is empty (Admin)",
            f"{self.emoji['user']} **/blacklist <add/remove> <user>** - Manage blacklisted users (Admin)",
            f"{self.emoji['role']} **/role_config <add/remove> <role>** - Manage role whitelist (Admin)"
        ]
        embed = self.create_embed(
            f"{self.emoji['music']} SporkMP3 Bot Commands",
            f"{self.emoji['cd']} **File Upload:** Mention the bot and attach an MP3 file\n\n" +
            "**Available Commands:**\n" + "\n".join(commands_list),
            discord.Color.blue()
        )
        
        # Add quick tips field
        embed.add_field(
            name=f"{self.emoji['success']} Quick Tips",
            value="• Upload MP3 files by mentioning the bot\n"
                  "• Use /playing to see current track progress\n"
                  "• Use /queue to manage your playlist",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed)

def setup(bot):
    bot.add_cog(Music(bot))