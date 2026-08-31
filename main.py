import discord
from discord.ext import commands
import json
import os
import logging
import time
from datetime import datetime
from music import Music

# logging setup
def setup_logging():
    if not os.path.exists('logs'):
        os.makedirs('logs')
        
    log_file = f'logs/bot_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

class SporkMP3(commands.AutoShardedBot):
    def __init__(self):
        # minimal intents for required operations
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        intents.guild_messages = True
        
        super().__init__(command_prefix="!", intents=intents)
        
        # track startup for health monitoring
        self.start_time = time.time()
        
        # load config or raise
        try:
            with open('config.json', 'r') as f:
                self.config = json.load(f)
        except FileNotFoundError:
            logging.error("config.json not found!")
            raise
        except json.JSONDecodeError:
            logging.error("config.json is invalid!")
            raise

    async def setup_hook(self):
        try:
            # add music cog to bot
            await self.add_cog(Music(self))
            
            # run file validation at startup
            music_cog = self.get_cog('Music')
            if music_cog:
                logging.info("Running startup file validation...")
                orphaned_count = music_cog.db.validate_files()
                if orphaned_count > 0:
                    logging.info(f"Startup validation completed: {orphaned_count} orphaned entries cleaned")
                else:
                    logging.info("Startup validation completed: All files valid")
            
            # sync app commands with discord
            await self.tree.sync()
            logging.info("Command tree synced successfully")
            
        except Exception as e:
            logging.error(f"Error in setup_hook: {e}")
            raise

    async def on_ready(self):
        logging.info(f'{self.user} is ready!')
        logging.info(f'Serving in {len(self.guilds)} servers')
        
        # set activity status
        activity = discord.Game(name="your audio files")
        await self.change_presence(activity=activity)
        logging.info("Set activity status to 'Playing your audio files'")
        
        # log startup time
        startup_time = time.time() - self.start_time
        logging.info(f"Bot startup completed in {startup_time:.2f} seconds")

    async def on_error(self, event_method: str, *args, **kwargs):
        logging.error(f'Error in {event_method}: ', exc_info=True)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        logging.error(f'Command error: {error}')

    async def on_voice_state_update(self, member, before, after):
        # handled by music cog with additional monitoring
        pass

    async def on_guild_join(self, guild):
        logging.info(f"Joined new guild: {guild.name} (ID: {guild.id}) with {guild.member_count} members")

    async def on_guild_remove(self, guild):
        logging.info(f"Left guild: {guild.name} (ID: {guild.id})")
        
        # cleanup guild data and state
        try:
            music_cog = self.get_cog('Music')
            if music_cog:
                # cleanup guild state
                if guild.id in music_cog.state.guild_states:
                    guild_state = music_cog.state.guild_states[guild.id]

                    # mark files inactive and cleanup
                    for track in guild_state.queue:
                        if not track.is_permanent:
                            track.cleanup()

                    # remove guild state entirely
                    del music_cog.state.guild_states[guild.id]
                    
                    logging.info(f"Cleaned up state for guild {guild.id}")
        except Exception as e:
            logging.error(f"Error cleaning up guild {guild.id}: {e}")

def main():
    # pin cwd to script location for stable relative paths
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    setup_logging()
    logging.info("Starting SporkMP3 bot...")
    
    # create required directories
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        required_dirs = ['temp', 'logs', 'permanent']
        for directory in required_dirs:
            if not os.path.exists(directory):
                os.makedirs(directory)
                logging.info(f"Created directory: {directory}")
    
        # initialize and run bot
        bot = SporkMP3()
        
        # handle main run loop errors
        try:
            bot.run(config['token'])
        except discord.LoginFailure:
            logging.critical("Invalid bot token provided!")
            raise
        except discord.HTTPException as e:
            logging.critical(f"HTTP error occurred: {e}")
            raise
        except KeyboardInterrupt:
            logging.info("Bot shutdown requested by user")
        except Exception as e:
            logging.critical(f"Unexpected error during bot runtime: {e}")
            raise
            
    except FileNotFoundError:
        logging.critical("config.json file not found!")
        raise
    except json.JSONDecodeError:
        logging.critical("Invalid JSON in config.json!")
        raise
    except KeyError as e:
        logging.critical(f"Missing required config key: {e}")
        raise
    except Exception as e:
        logging.critical(f"Fatal error: {e}")
        raise
    finally:
        logging.info("Bot shutdown complete")

if __name__ == "__main__":
    main()
