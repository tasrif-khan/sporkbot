import os
import time
import aiohttp
import logging
from mutagen.mp3 import MP3
from datetime import timedelta

class AudioTrack:
    def __init__(self, url, filename, requester, file_size):
        self.url = url
        self.filename = filename
        self.requester = requester
        self.position = 0
        self.duration = 0
        self.downloaded_path = None
        self.file_size = file_size
        self.last_accessed = time.time()
        self.download_retries = 3
        self.volume = 100  # Default volume level

    async def download(self, temp_folder):
        """Download the audio file and get its metadata"""
        # Create a safe filename to prevent path traversal
        safe_filename = ''.join(c for c in self.filename if c.isalnum() or c in '._- ')
        self.downloaded_path = os.path.join(temp_folder, safe_filename)
        
        for attempt in range(self.download_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(self.url) as resp:
                        if resp.status == 200:
                            with open(self.downloaded_path, 'wb') as f:
                                while True:
                                    chunk = await resp.content.read(8192)
                                    if not chunk:
                                        break
                                    f.write(chunk)
                            
                            # Get audio metadata
                            audio = MP3(self.downloaded_path)
                            self.duration = audio.info.length
                            self.last_accessed = time.time()
                            logging.info(f"Successfully downloaded: {self.filename}")
                            return
                        else:
                            logging.warning(f"Download failed with status {resp.status}: {self.filename}")
            except Exception as e:
                logging.error(f"Download attempt {attempt + 1} failed for {self.filename}: {str(e)}")
                if os.path.exists(self.downloaded_path):
                    os.remove(self.downloaded_path)
                if attempt == self.download_retries - 1:
                    raise

    def cleanup(self):
        """Remove the downloaded file and reset track state"""
        if self.downloaded_path and os.path.exists(self.downloaded_path):
            try:
                os.remove(self.downloaded_path)
                logging.info(f"Cleaned up file: {self.filename}")
            except Exception as e:
                logging.error(f"Error removing file {self.downloaded_path}: {e}")
            finally:
                self.downloaded_path = None
                self.position = 0  # Reset position on cleanup

    def to_dict(self):
        """Convert track information to dictionary for display"""
        return {
            'filename': self.filename,
            'requester': self.requester,
            'duration': str(timedelta(seconds=int(self.duration))),
            'position': str(timedelta(seconds=int(self.position))),
            'size_mb': self.file_size / (1024 * 1024),
            'volume': self.volume
        }

class TrackManager:
    def __init__(self, config):
        # Basic configuration
        self.max_queue_size = config['max_queue_size_mb'] * 1024 * 1024
        self.temp_folder = config['temp_folder']
        self.default_volume = config['default_volume']

        # Resource limits from config
        self.cleanup_interval = config['resource_limits']['cleanup_interval_minutes'] * 60  # Convert to seconds
        self.file_max_age = config['resource_limits']['inactive_timeout_minutes'] * 60  # Convert to seconds
        self.max_tracks = config['resource_limits']['max_tracks_per_guild']
        self.max_duration = config['resource_limits']['max_track_duration_minutes'] * 60  # Convert to seconds
        self.rate_limit = config['resource_limits']['rate_limit_seconds']

        # Internal state
        self.last_cleanup = time.time()

    def get_queue_size(self, queue):
        """Calculate total size of all tracks in queue"""
        return sum(track.file_size for track in queue)

    def can_add_to_queue(self, queue, file_size):
        """Check if a new file can be added to queue without exceeding limits"""
        # Check queue size limit
        if (self.get_queue_size(queue) + file_size) > self.max_queue_size:
            logging.warning(f"Queue size limit reached: {self.max_queue_size/1024/1024}MB")
            return False
            
        # Check track count limit
        if len(queue) >= self.max_tracks:
            logging.warning(f"Maximum track count reached: {self.max_tracks}")
            return False
            
        return True

    async def validate_track(self, track):
        """Validate track duration and other constraints"""
        if track.duration > self.max_duration:
            raise ValueError(
                f"Track duration ({track.duration/60:.1f}min) exceeds limit "
                f"of {self.max_duration/60:.1f} minutes"
            )
        return True

    def get_queue_stats(self, queue):
        """Get queue statistics"""
        total_size = self.get_queue_size(queue)
        return {
            'current_size_mb': total_size / (1024 * 1024),
            'max_size_mb': self.max_queue_size / (1024 * 1024),
            'available_space_mb': (self.max_queue_size - total_size) / (1024 * 1024),
            'track_count': len(queue),
            'max_tracks': self.max_tracks,
            'tracks_remaining': self.max_tracks - len(queue)
        }

    async def cleanup_temp_files(self):
        """Clean up old temporary files"""
        current_time = time.time()
        
        # Only run cleanup if enough time has passed
        if current_time - self.last_cleanup < self.cleanup_interval:
            return

        self.last_cleanup = current_time
        cleaned_count = 0
        error_count = 0

        if os.path.exists(self.temp_folder):
            for file in os.listdir(self.temp_folder):
                file_path = os.path.join(self.temp_folder, file)
                try:
                    # Check file age
                    file_age = current_time - os.path.getctime(file_path)
                    if file_age > self.file_max_age:
                        os.remove(file_path)
                        cleaned_count += 1
                        logging.info(f"Cleaned up old file: {file}")
                except Exception as e:
                    logging.error(f"Error cleaning up file {file}: {e}")
                    error_count += 1

        if cleaned_count > 0 or error_count > 0:
            logging.info(
                f"Cleanup completed: {cleaned_count} files removed, "
                f"{error_count} errors encountered"
            )

    async def ensure_temp_folder(self):
        """Ensure temporary folder exists and is writable"""
        if not os.path.exists(self.temp_folder):
            try:
                os.makedirs(self.temp_folder)
                logging.info(f"Created temporary folder: {self.temp_folder}")
            except Exception as e:
                logging.error(f"Error creating temp folder: {e}")
                raise

        # Test if folder is writable
        test_file = os.path.join(self.temp_folder, 'test_write')
        try:
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
        except Exception as e:
            logging.error(f"Temp folder is not writable: {e}")
            raise

    async def initialize(self):
        """Initialize the track manager"""
        try:
            await self.ensure_temp_folder()
            await self.cleanup_temp_files()  # Initial cleanup
            logging.info("Track manager initialized successfully")
        except Exception as e:
            logging.error(f"Failed to initialize track manager: {e}")
            raise

    def is_rate_limited(self, last_action_time):
        """Check if an action should be rate limited"""
        return (time.time() - last_action_time) < self.rate_limit