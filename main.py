import discord
from discord.ext import commands
import json
import os
import logging
from datetime import datetime
from cogs.music import Music
from utils.monitoring import BotMonitor

# Set up logging
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

class SporkMP3(commands.Bot):
    def __init__(self):
        # Set up minimal intents
        intents = discord.Intents.none()
        intents.guilds = True  # Needed for basic guild operations
        intents.voice_states = True  # Needed for voice functionality
        intents.guild_messages = True  # Needed for message handling
        
        super().__init__(command_prefix="!", intents=intents)
        
        # Load config
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
            await self.add_cog(Music(self))
            await self.tree.sync()
        except Exception as e:
            logging.error(f"Error in setup_hook: {e}")
            raise

    async def on_ready(self):
        logging.info(f'{self.user} is ready!')
        logging.info(f'Serving in {len(self.guilds)} servers')

    async def on_error(self, event_method: str, *args, **kwargs):
        logging.error(f'Error in {event_method}: ', exc_info=True)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        logging.error(f'Command error: {error}')

def main():
    setup_logging()
    
    # Create required directories
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        
        required_dirs = ['temp', 'logs']
        for directory in required_dirs:
            if not os.path.exists(directory):
                os.makedirs(directory)
                logging.info(f"Created directory: {directory}")
    
        # Initialize and run bot
        bot = SporkMP3()
        bot.run(config['token'])
    except Exception as e:
        logging.critical(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    main()