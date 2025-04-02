import discord
import logging
import asyncio
from utils.permission_checks import check_permissions, admin_only

class MusicCommands:
    """Handles command logic for the music bot - but doesn't register commands"""
    def __init__(self, bot, db, track_manager, music_state, music_ui, music_playback):
        self.bot = bot
        self.db = db
        self.track_manager = track_manager
        self.music_state = music_state
        self.music_ui = music_ui
        self.music_playback = music_playback
    
    # NOTE: These are not commands themselves, just methods implementing command logic
    
    async def blacklist(self, interaction, action, user):
        try:
            if action.lower() not in ['add', 'remove']:
                embed = self.music_ui.create_embed(
                    f"{self.music_ui.emoji['warning']} Invalid Action",
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

            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['success']} Blacklist Updated",
                f"{user.mention} has been {action_text} the blacklist.",
                discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error managing blacklist: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Error",
                "Failed to update blacklist.",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    async def role_config(self, interaction, action, role):
        try:
            if action.lower() not in ['add', 'remove']:
                embed = self.music_ui.create_embed(
                    f"{self.music_ui.emoji['warning']} Invalid Action",
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

            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['success']} Role Whitelist Updated",
                f"{role.mention} has been {action_text} the role whitelist.",
                discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error managing role whitelist: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Error",
                "Failed to update role whitelist.",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    async def autodisconnect(self, interaction, enabled):
        try:
            self.db.set_autodisconnect_setting(interaction.guild_id, enabled)
            status = "enabled" if enabled else "disabled"
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['success']} Auto-Disconnect Updated",
                f"Auto-disconnect when queue is empty has been {status}.",
                discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error setting autodisconnect: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Error",
                "Failed to update auto-disconnect setting.",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    async def autoplay(self, interaction, enabled):
        try:
            self.db.set_autoplay_setting(interaction.guild_id, enabled)
            status = "enabled" if enabled else "disabled"
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['success']} Autoplay Updated",
                f"Autoplay has been {status}.",
                discord.Color.green()
            )
            self.music_state.alone_since.pop(interaction.guild_id, None)
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error setting autoplay: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Error",
                "Failed to update autoplay setting.",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)
    
    async def play(self, interaction):
        # Defer response immediately
        await interaction.response.defer()
        
        try:
            # Check if user is in a voice channel
            if not interaction.user.voice:
                embed = self.music_ui.create_embed(
                    f"{self.music_ui.emoji['warning']} Voice Channel Required",
                    "You need to be in a voice channel to use this command!",
                    discord.Color.yellow()
                )
                await interaction.followup.send(embed=embed)
                return

            # Get guild state and check queue
            guild_state = await self.music_state.get_guild_state(interaction.guild_id)
            if not guild_state.queue:
                embed = self.music_ui.create_embed(
                    f"{self.music_ui.emoji['warning']} Empty Queue",
                    "No songs in queue! Add some audio files first.",
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
                    embed = self.music_ui.create_embed(
                        f"{self.music_ui.emoji['error']} Connection Failed",
                        "Failed to connect to voice channel!",
                        discord.Color.red()
                    )
                    await interaction.followup.send(embed=embed)
                    return

            # Check if already playing
            if voice_client.is_playing():
                embed = self.music_ui.create_embed(
                    f"{self.music_ui.emoji['warning']} Already Playing",
                    "A track is already playing! Use /skip to play the next track.",
                    discord.Color.yellow()
                )
                await interaction.followup.send(embed=embed)
                return

            try:
                # Attempt to play next track with force_play=True to override autoplay setting
                await self.music_playback.play_next(interaction.guild, force_play=True)
                
                # Check if track started playing successfully
                if guild_state.current_track:
                    # Get current position using the new tracking method
                    current_position = guild_state.current_track.get_current_position()
                    
                    # Calculate progress bar
                    progress_bar = self.music_ui.create_progress_bar(
                        current_position, 
                        guild_state.current_track.duration
                    )
                    
                    # Create success embed with detailed information
                    embed = self.music_ui.create_embed(
                        f"{self.music_ui.emoji['play']} Now Playing",
                        f"{self.music_ui.emoji['music']} **Track:** {guild_state.current_track.filename}\n"
                        f"{self.music_ui.emoji['microphone']} **Requested by:** {guild_state.current_track.requester}\n"
                        f"{self.music_ui.emoji['time']} **Duration:** {self.music_ui.format_duration(int(guild_state.current_track.duration))}\n"
                        f"🎚️ **Bitrate:** {guild_state.current_track.bitrate}kbps\n"
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
                    
                    await interaction.followup.send(embed=embed)
                else:
                    embed = self.music_ui.create_embed(
                        f"{self.music_ui.emoji['error']} Playback Failed",
                        "Failed to start playback. Please try again or check the file.",
                        discord.Color.red()
                    )
                    await interaction.followup.send(embed=embed)

            except Exception as e:
                logging.error(f"Error playing track: {e}")
                embed = self.music_ui.create_embed(
                    f"{self.music_ui.emoji['error']} Playback Error",
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
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Error",
                "An unexpected error occurred while processing the command.",
                discord.Color.red()
            )
            await interaction.followup.send(embed=embed)
            
    async def pause(self, interaction):
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_playing():
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            guild_state = await self.music_state.get_guild_state(interaction.guild_id)
            
            # Mark track as paused
            if guild_state.current_track:
                guild_state.current_track.pause_playback()
                
            voice_client.pause()
            
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['pause']} Paused",
                f"Paused: **{guild_state.current_track.filename}**\n"
                f"Use `/resume` to continue playback",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error pausing playback: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Pause Error",
                "Failed to pause the music!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    async def resume(self, interaction):
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_paused():
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Not Paused",
                "Nothing is currently paused!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            guild_state = await self.music_state.get_guild_state(interaction.guild_id)
            
            # Resume track timing
            if guild_state.current_track:
                guild_state.current_track.resume_playback()
                
            voice_client.resume()
            
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['resume']} Resumed",
                f"Resumed: **{guild_state.current_track.filename}**",
                discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error resuming playback: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Resume Error",
                "Failed to resume the music!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    async def queue(self, interaction):
        guild_state = await self.music_state.get_guild_state(interaction.guild_id)
        if not guild_state.queue:
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['queue']} Queue Empty",
                "No tracks in queue! Add some audio files to get started.",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            current_size_mb = self.track_manager.get_queue_size(guild_state.queue) / (1024 * 1024)
            max_size_mb = self.bot.config['max_queue_size_mb']
            
            queue_list = "\n".join(
                f"`{idx + 1}.` {self.music_ui.emoji['music']} **{track.filename}**\n"
                f"┗ {self.music_ui.emoji['microphone']} Requested by: {track.requester}"
                for idx, track in enumerate(guild_state.queue)
            )
            
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['queue']} Current Queue",
                f"{queue_list}\n\n"
                f"{self.music_ui.emoji['cd']} **Queue Size:** {current_size_mb:.1f}MB / {max_size_mb}MB\n"
                f"{self.music_ui.emoji['music']} **Tracks in Queue:** {len(guild_state.queue)}",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error displaying queue: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Queue Error",
                "Failed to display the queue!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    async def playing(self, interaction):
        guild_state = await self.music_state.get_guild_state(interaction.guild_id)
        if not guild_state.current_track:
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            # Get current position using the new tracking method
            current_position = guild_state.current_track.get_current_position()
            
            # Format the position and duration
            current_position_str = self.music_ui.format_duration(int(current_position))
            total_duration = self.music_ui.format_duration(int(guild_state.current_track.duration))
            
            # Create progress bar
            progress_bar = self.music_ui.create_progress_bar(
                current_position,
                guild_state.current_track.duration
            )
            
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['play']} Now Playing",
                f"{self.music_ui.emoji['music']} **Track:** {guild_state.current_track.filename}\n"
                f"{self.music_ui.emoji['microphone']} **Requested by:** {guild_state.current_track.requester}\n"
                f"{self.music_ui.emoji['time']} **Time:** `{current_position_str} / {total_duration}`\n"
                f"🎚️ **Bitrate:** {guild_state.current_track.bitrate}kbps\n"
                f"**Progress:** {progress_bar}",
                discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error displaying current track: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Display Error",
                "Failed to display current track info!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)
            
    async def volume(self, interaction, volume):
        if volume < 0 or volume > 120:
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Invalid Volume",
                "Volume must be between 0 and 120!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            guild_state = await self.music_state.get_guild_state(interaction.guild_id)
            guild_state.volume = volume
            
            voice_client = interaction.guild.voice_client
            if voice_client and voice_client.source:
                voice_client.source.volume = volume / 100
            
            # Choose appropriate volume emoji
            volume_emoji = self.music_ui.emoji['volume']
            if volume == 0:
                volume_emoji = self.music_ui.emoji['mute']
            elif volume < 50:
                volume_emoji = self.music_ui.emoji['low_volume']
            
            embed = self.music_ui.create_embed(
                f"{volume_emoji} Volume Updated",
                f"Set volume to **{volume}%**",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error setting volume: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Volume Error",
                "Failed to set volume!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    async def forward(self, interaction, seconds):
        guild_state = await self.music_state.get_guild_state(interaction.guild_id)
        if not guild_state.current_track:
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        if seconds <= 0:
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Invalid Time",
                "Please specify a positive number of seconds!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            # Get current position using tracking method
            current_pos = guild_state.current_track.get_current_position()
            new_position = min(current_pos + seconds, guild_state.current_track.duration - 1)
            
            voice_client = interaction.guild.voice_client
            if not voice_client or not voice_client.is_connected():
                embed = self.music_ui.create_embed(
                    f"{self.music_ui.emoji['error']} Not Connected",
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
            
            audio_source = self.music_playback.get_pcm_audio(guild_state.current_track, new_position)
            
            def after_seeking(error):
                guild_state.is_seeking = False
                if error:
                    logging.error(f'Seeking error: {error}')
            
            voice_client.play(audio_source, after=after_seeking)
            
            # Calculate actual seconds moved forward
            actual_skip = new_position - current_pos
            
            # Calculate progress bar
            progress_bar = self.music_ui.create_progress_bar(
                new_position,
                guild_state.current_track.duration
            )
            
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['time']} Skipped Forward",
                f"{self.music_ui.emoji['music']} **Track:** {guild_state.current_track.filename}\n"
                f"{self.music_ui.emoji['time']} **Skipped:** {actual_skip} seconds forward\n"
                f"**New Position:** {self.music_ui.format_duration(new_position)} / {self.music_ui.format_duration(int(guild_state.current_track.duration))}\n"
                f"**Progress:** {progress_bar}",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            guild_state.is_seeking = False
            logging.error(f"Error during forward seek: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Forward Error",
                "An error occurred while seeking forward!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    async def backward(self, interaction, seconds):
        guild_state = await self.music_state.get_guild_state(interaction.guild_id)
        if not guild_state.current_track:
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        if seconds <= 0:
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Invalid Time",
                "Please specify a positive number of seconds!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            # Get current position using tracking method
            current_pos = guild_state.current_track.get_current_position()
            new_position = max(0, current_pos - seconds)
            
            voice_client = interaction.guild.voice_client
            if not voice_client or not voice_client.is_connected():
                embed = self.music_ui.create_embed(
                    f"{self.music_ui.emoji['error']} Not Connected",
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
            
            audio_source = self.music_playback.get_pcm_audio(guild_state.current_track, new_position)
            
            def after_seeking(error):
                guild_state.is_seeking = False
                if error:
                    logging.error(f'Seeking error: {error}')
            
            voice_client.play(audio_source, after=after_seeking)
            
            # Calculate actual seconds moved backward
            actual_skip = current_pos - new_position
            
            # Calculate progress bar
            progress_bar = self.music_ui.create_progress_bar(
                new_position,
                guild_state.current_track.duration
            )
            
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['time']} Skipped Backward",
                f"{self.music_ui.emoji['music']} **Track:** {guild_state.current_track.filename}\n"
                f"{self.music_ui.emoji['time']} **Skipped:** {actual_skip} seconds backward\n"
                f"**New Position:** {self.music_ui.format_duration(new_position)} / {self.music_ui.format_duration(int(guild_state.current_track.duration))}\n"
                f"**Progress:** {progress_bar}",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            guild_state.is_seeking = False
            logging.error(f"Error during backward seek: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Backward Error",
                "An error occurred while seeking backward!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)
            
    async def timestamp(self, interaction, hours, minutes, seconds):
        guild_state = await self.music_state.get_guild_state(interaction.guild_id)
        if not guild_state.current_track:
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        if hours < 0 or minutes < 0 or seconds < 0 or seconds >= 60 or minutes >= 60:
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Invalid Time",
                "Invalid timestamp! Format: hours >= 0, 0 <= minutes < 60, 0 <= seconds < 60",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        new_position = (hours * 3600) + (minutes * 60) + seconds
        
        if new_position >= guild_state.current_track.duration:
            total_duration = self.music_ui.format_duration(int(guild_state.current_track.duration))
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Invalid Position",
                f"Cannot seek beyond the end of the track! Maximum duration is {total_duration}",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Not Connected",
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
            
            audio_source = self.music_playback.get_pcm_audio(guild_state.current_track, guild_state.current_track.position)
            
            def after_seeking(error):
                guild_state.is_seeking = False
                if error:
                    logging.error(f'Seeking error: {error}')
            
            voice_client.play(audio_source, after=after_seeking)
            
            new_timestamp = self.music_ui.format_duration(new_position)
            total_duration = self.music_ui.format_duration(int(guild_state.current_track.duration))
            
            # Calculate progress bar
            progress_bar = self.music_ui.create_progress_bar(
                new_position,
                guild_state.current_track.duration
            )
            
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['time']} Position Updated",
                f"{self.music_ui.emoji['music']} **Track:** {guild_state.current_track.filename}\n"
                f"{self.music_ui.emoji['time']} **New Position:** {new_timestamp} / {total_duration}\n"
                f"**Progress:** {progress_bar}",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            guild_state.is_seeking = False
            logging.error(f"Error during timestamp seek: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Seeking Error",
                "An error occurred while seeking!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    async def skip(self, interaction):
        guild_state = await self.music_state.get_guild_state(interaction.guild_id)
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_playing():
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return

        try:
            skipped_track = guild_state.current_track.filename
            voice_client.stop()  # This will trigger play_next via the after callback
            
            # Show next track info if available
            next_track_info = (f"\n{self.music_ui.emoji['play']} **Up next:** {guild_state.queue[0].filename}" 
                             if guild_state.queue else "\nQueue is now empty")
            
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['skip']} Track Skipped",
                f"{self.music_ui.emoji['music']} **Skipped:** {skipped_track}{next_track_info}",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error skipping track: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Skip Error",
                "Failed to skip the track!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    async def clear(self, interaction):
        guild_state = await self.music_state.get_guild_state(interaction.guild_id)
        try:
            queue_length = len(guild_state.queue)
            # Clean up all queued tracks
            for track in guild_state.queue:
                track.cleanup()
            
            guild_state.queue.clear()
            
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['success']} Queue Cleared",
                f"{self.music_ui.emoji['music']} Cleared {queue_length} tracks from the queue\n"
                f"{self.music_ui.emoji['play']} Current track remains playing",
                discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error clearing queue: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Clear Error",
                "Failed to clear the queue!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)  

    async def stop(self, interaction):
        """Stop the current playback without clearing the queue"""
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_playing():
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return
    
        try:
            guild_state = await self.music_state.get_guild_state(interaction.guild_id)
            current_track_name = guild_state.current_track.filename if guild_state.current_track else "Unknown"
            
            voice_client.stop()
            
            if guild_state.current_track:
                self.track_manager.mark_file_inactive(guild_state.current_track.downloaded_path)
                guild_state.current_track.cleanup()
                guild_state.current_track = None
    
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['stop']} Playback Stopped",
                f"{self.music_ui.emoji['music']} **Stopped playing:** {current_track_name}\n"
                f"{self.music_ui.emoji['queue']} Queue remains unchanged",
                discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            logging.error(f"Error stopping playback: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Stop Error",
                "Failed to stop the playback!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    async def disconnect(self, interaction):
        guild_state = await self.music_state.get_guild_state(interaction.guild_id)
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_connected():
            try:
                if guild_state.current_track:
                    self.track_manager.mark_file_inactive(guild_state.current_track.downloaded_path)
                    guild_state.current_track.cleanup()
                    guild_state.current_track = None
                
                for track in guild_state.queue:
                    track.cleanup()
                queue_length = len(guild_state.queue)
                guild_state.queue.clear()
                
                await voice_client.disconnect()
                
                embed = self.music_ui.create_embed(
                    f"{self.music_ui.emoji['disconnect']} Disconnected",
                    f"{self.music_ui.emoji['success']} Successfully disconnected from voice channel\n"
                    f"{self.music_ui.emoji['queue']} Cleared {queue_length} tracks from queue\n"
                    f"{self.music_ui.emoji['stop']} All resources cleaned up",
                    discord.Color.blue()
                )
                await interaction.response.send_message(embed=embed)
            except Exception as e:
                logging.error(f"Error disconnecting: {e}")
                embed = self.music_ui.create_embed(
                    f"{self.music_ui.emoji['error']} Disconnect Error",
                    "Failed to disconnect properly!",
                    discord.Color.red()
                )
                await interaction.response.send_message(embed=embed)
        else:
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Not Connected",
                "Not connected to a voice channel!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)

    async def loop(self, interaction, times=None):
        guild_state = await self.music_state.get_guild_state(interaction.guild_id)
        
        if not guild_state.current_track:
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Not Playing",
                "Nothing is currently playing!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return
    
        try:
            if times is not None and times <= 0:
                embed = self.music_ui.create_embed(
                    f"{self.music_ui.emoji['warning']} Invalid Input",
                    "Please specify a positive number of loops or leave empty for infinite loop.",
                    discord.Color.yellow()
                )
                await interaction.response.send_message(embed=embed)
                return
    
            # Toggle loop mode
            guild_state.loop_enabled = not guild_state.loop_enabled if times is None else True
            guild_state.max_loops = times
            guild_state.loop_count = 0
    
            if guild_state.loop_enabled:
                loop_msg = "infinitely" if times is None else f"{times} times"
                embed = self.music_ui.create_embed(
                    f"{self.music_ui.emoji['success']} Loop Enabled",
                    f"{self.music_ui.emoji['music']} **Track:** {guild_state.current_track.filename}\n"
                    f"🔄 **Loop Mode:** Will loop {loop_msg}",
                    discord.Color.green()
                )
            else:
                embed = self.music_ui.create_embed(
                    f"{self.music_ui.emoji['success']} Loop Disabled",
                    f"{self.music_ui.emoji['music']} **Track:** {guild_state.current_track.filename}\n"
                    f"🔄 **Loop Mode:** Disabled",
                    discord.Color.blue()
                )
    
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            logging.error(f"Error toggling loop mode: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Loop Error",
                "Failed to toggle loop mode!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)
    
    async def remove(self, interaction, position):
        """Remove a specific song from the queue"""
        guild_state = await self.music_state.get_guild_state(interaction.guild_id)
        
        # Check if queue is empty
        if not guild_state.queue:
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Empty Queue",
                "The queue is empty!",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return
        
        # Check if position is valid
        if position < 1 or position > len(guild_state.queue):
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['warning']} Invalid Position",
                f"Please enter a valid position between 1 and {len(guild_state.queue)}",
                discord.Color.yellow()
            )
            await interaction.response.send_message(embed=embed)
            return
        
        try:
            # Remove the track (position-1 because users will see queue starting at 1)
            removed_track = guild_state.queue.pop(position-1)
            
            # Clean up the removed track
            removed_track.cleanup()
            
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['success']} Track Removed",
                f"{self.music_ui.emoji['music']} Removed: **{removed_track.filename}**\n"
                f"{self.music_ui.emoji['queue']} Queue position: **#{position}**\n"
                f"{self.music_ui.emoji['user']} Requested by: {removed_track.requester}",
                discord.Color.green()
            )
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            logging.error(f"Error removing track: {e}")
            embed = self.music_ui.create_embed(
                f"{self.music_ui.emoji['error']} Error",
                "Failed to remove the track from queue!",
                discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)

    async def help(self, interaction):
        commands_list = [
            f"{self.music_ui.emoji['play']} **/play** - Play the current loaded MP3",
            f"{self.music_ui.emoji['stop']} **/stop** - Stop the current playback",
            f"{self.music_ui.emoji['pause']} **/pause** - Pause the current song",
            f"{self.music_ui.emoji['resume']} **/resume** - Resume the current song",
            f"{self.music_ui.emoji['queue']} **/queue** - Show the current queue",
            f"{self.music_ui.emoji['music']} **/playing** - Show what's currently playing",
            f"{self.music_ui.emoji['stop']} **/clear** - Clear the queue",
            f"{self.music_ui.emoji['queue']} **/remove <position>** - Remove a specific song from the queue",
            f"{self.music_ui.emoji['volume']} **/volume <0-120>** - Set the volume",
            f"{self.music_ui.emoji['skip']} **/skip** - Skip the current song",
            f"{self.music_ui.emoji['loop']} **/loop <times>** - Toggle loop mode (optional: specify number of loops)",
            f"{self.music_ui.emoji['time']} **/forward <seconds>** - Skip forward by specified seconds",
            f"{self.music_ui.emoji['time']} **/backward <seconds>** - Skip backward by specified seconds",
            f"{self.music_ui.emoji['time']} **/timestamp <hours> <minutes> <seconds>** - Set track position",
            f"{self.music_ui.emoji['disconnect']} **/disconnect** - Disconnect the bot\n",  # Add newline here
            f"{self.music_ui.emoji['settings']} **/autoplay <true/false>** - Enable or disable autoplay",
            f"{self.music_ui.emoji['settings']} **/autodisconnect <true/false>** - Enable/disable auto-disconnect when queue is empty (Admin)",
            f"{self.music_ui.emoji['user']} **/blacklist <add/remove> <user>** - Manage blacklisted users (Admin)",
            f"{self.music_ui.emoji['role']} **/role_config <add/remove> <role>** - Manage role whitelist (Admin)"
        ]
        embed = self.music_ui.create_embed(
            f"{self.music_ui.emoji['music']} SporkMP3 Bot Commands",
            f"{self.music_ui.emoji['cd']} **File Upload:** Mention the bot and attach an audio file(s) (up to 10 at once!)\n\n" +
            "**Available Commands:**\n" + "\n".join(commands_list),
            discord.Color.blue()
        )
        
        # Add quick tips field
        embed.add_field(
            name=f"{self.music_ui.emoji['success']} Quick Tips",
            value="• Upload audio files by mentioning the bot\n"
                  "• Use /playing to see current track progress\n"
                  "• Use /queue to manage your playlist\n"
                  "• MP4 audio is now supported!",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed)
