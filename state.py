"""
state management for the music bot.
tracks guild state, audio tracks, and playback position.
"""
import os
import time
import logging
import aiohttp
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from mutagen import File as MutagenFile
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from datetime import timedelta

from codec import ensure_playable


@dataclass
class AudioTrack:
    """represents audio track in the queue"""
    url: str
    filename: str
    requester: str
    file_size: int
    is_permanent: bool = False
    
    # set after download
    downloaded_path: Optional[str] = None
    converted_path: Optional[str] = None  # codec conversion output
    duration: float = 0
    bitrate: int = 192
    volume: int = 100
    
    # metadata - title, artist, cover
    title: Optional[str] = None
    artist: Optional[str] = None
    cover_url: Optional[str] = None  # discord cdn url

    # playback state tracking
    position: float = 0
    playback_start_time: Optional[float] = None
    paused_position: float = 0
    is_paused: bool = False
    last_accessed: float = field(default_factory=time.time)
    
    def get_display_name(self) -> str:
        """get best display name - metadata title artist or filename"""
        if self.title:
            if self.artist:
                return f"{self.artist} - {self.title}"
            return self.title
        
        # fallback to filename without extension
        name = os.path.splitext(self.filename)[0]
        name = name.replace('_', ' ')
        return name
    
    def get_artwork(self) -> Optional[bytes]:
        """extract raw cover art bytes from audio file"""
        path = self.playback_path or self.downloaded_path
        if not path or not os.path.exists(path):
            return None
        try:
            audio = MutagenFile(path)
            if audio is None:
                return None
            if isinstance(audio, MP3) and audio.tags:
                for key in audio.tags.keys():
                    if key.startswith('APIC'):
                        return audio.tags[key].data
            elif isinstance(audio, MP4) and audio.tags:
                covr = audio.tags.get('covr', [])
                if covr:
                    return bytes(covr[0])
            elif hasattr(audio, 'pictures') and audio.pictures:
                return audio.pictures[0].data
        except Exception as e:
            logging.debug(f"Artwork extraction failed for {path}: {e}")
        return None

    @property
    def playback_path(self) -> Optional[str]:
        """path for ffmpeg - converted file if available else original"""
        return self.converted_path or self.downloaded_path
    
    def get_metadata(self, file_path: str) -> float:
        """extract duration, bitrate, title, artist from file
        
        tries: extension parser -> mutagen -> ffprobe
        """
        audio = None
        ext = file_path.lower().split('.')[-1]
        
        # try extension-specific parser first
        try:
            if ext == 'mp3':
                audio = MP3(file_path)
            elif ext in ('mp4', 'm4a'):
                audio = MP4(file_path)
        except Exception as e:
            logging.debug(f"Extension-specific parser failed for {file_path}: {e}")
        
        # fallback to generic mutagen
        if audio is None:
            try:
                audio = MutagenFile(file_path)
            except Exception as e:
                logging.debug(f"MutagenFile fallback failed for {file_path}: {e}")
        
        # extract metadata if mutagen worked
        if audio is not None:
            try:
                self._extract_tags(audio)
            except Exception as e:
                logging.debug(f"Tag extraction failed for {file_path}: {e}")
                self.title = None
                self.artist = None
            
            # get bitrate
            if hasattr(audio.info, 'bitrate') and audio.info.bitrate:
                self.bitrate = audio.info.bitrate // 1000
            elif hasattr(audio.info, 'length') and audio.info.length > 0:
                self.bitrate = int(os.path.getsize(file_path) * 8 / audio.info.length / 1000)
            else:
                self.bitrate = 192
            
            # get duration
            duration = getattr(audio.info, 'length', None) or getattr(audio.info, 'duration', None)
            if duration and duration > 0:
                display_name = self.get_display_name()
                logging.info(f"Extracted metadata: {display_name} - {duration:.1f}s @ {self.bitrate}kbps")
                return duration
        
        # ffprobe fallback for mislabeled or rare codec files
        logging.info(f"Mutagen failed, falling back to ffprobe for {file_path}")
        return self._get_metadata_ffprobe(file_path)
    
    def _extract_tags(self, audio):
        """extract title and artist tags based on mutagen type"""
        self.title = None
        self.artist = None
        
        if not hasattr(audio, 'tags') or not audio.tags:
            return
        
        if isinstance(audio, MP3):
            self.title = str(audio.tags.get('TIT2', [None])[0]) if 'TIT2' in audio.tags else None
            self.artist = str(audio.tags.get('TPE1', [None])[0]) if 'TPE1' in audio.tags else None
        elif isinstance(audio, MP4):
            self.title = audio.tags.get('\xa9nam', [None])[0] if '\xa9nam' in audio.tags else None
            self.artist = audio.tags.get('\xa9ART', [None])[0] if '\xa9ART' in audio.tags else None
        else:
            self.title = audio.tags.get('title', [None])[0] if 'title' in audio.tags else None
            self.artist = audio.tags.get('artist', [None])[0] if 'artist' in audio.tags else None
        
        if self.title:
            self.title = str(self.title).strip()
        if self.artist:
            self.artist = str(self.artist).strip()
    
    def _get_metadata_ffprobe(self, file_path: str) -> float:
        """extract duration and bitrate using ffprobe synchronously"""
        import subprocess
        import json as _json
        
        try:
            result = subprocess.run(
                [
                    'ffprobe', '-v', 'quiet',
                    '-select_streams', 'a:0',
                    '-show_entries', 'stream=bit_rate,codec_name',
                    '-show_entries', 'format=duration',
                    '-show_entries', 'format_tags=title,artist',
                    '-of', 'json',
                    file_path
                ],
                capture_output=True, text=True, timeout=15
            )
            
            if result.returncode != 0:
                raise ValueError(f"ffprobe failed for {file_path}")
            
            data = _json.loads(result.stdout)
            
            # get duration
            duration = float(data.get('format', {}).get('duration', 0))
            if duration <= 0:
                raise ValueError(f"Cannot determine duration: {file_path}")
            
            # get bitrate
            streams = data.get('streams', [])
            if streams and streams[0].get('bit_rate'):
                self.bitrate = int(streams[0]['bit_rate']) // 1000
            else:
                self.bitrate = 192
            
            # get tags from format metadata
            tags = data.get('format', {}).get('tags', {})
            if tags:
                self.title = self.title or tags.get('title') or tags.get('TITLE')
                self.artist = self.artist or tags.get('artist') or tags.get('ARTIST')
            
            display_name = self.get_display_name()
            logging.info(f"Extracted metadata (ffprobe): {display_name} - {duration:.1f}s @ {self.bitrate}kbps")
            return duration
            
        except Exception as e:
            logging.error(f"ffprobe metadata extraction failed for {file_path}: {e}")
            raise
    
    async def download(self, temp_folder: str, retries: int = 3):
        """download audio file from url with retries"""
        safe_name = ''.join(c for c in self.filename if c.isalnum() or c in '._- ')
        self.downloaded_path = os.path.join(temp_folder, safe_name)
        
        for attempt in range(retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(self.url) as resp:
                        if resp.status == 200:
                            with open(self.downloaded_path, 'wb') as f:
                                async for chunk in resp.content.iter_chunked(8192):
                                    f.write(chunk)
                            
                            self.duration = self.get_metadata(self.downloaded_path)
                            self.last_accessed = time.time()
                            logging.info(f"Downloaded: {self.filename}")
                            
                            # probe codec and convert if needed
                            await self._ensure_compatible(temp_folder)
                            return
                        logging.warning(f"Download failed (status {resp.status}): {self.filename}")
            except Exception as e:
                logging.error(f"Download attempt {attempt + 1} failed: {e}")
                if self.downloaded_path and os.path.exists(self.downloaded_path):
                    os.remove(self.downloaded_path)
                if attempt == retries - 1:
                    raise
    
    async def _ensure_compatible(self, temp_folder: str):
        """probe file and convert if codec incompatible"""
        try:
            playable_path, was_converted = await ensure_playable(
                self.downloaded_path, temp_folder
            )
            if was_converted:
                self.converted_path = playable_path
                # reread duration from converted if changed
                try:
                    self.duration = self.get_metadata(playable_path)
                except Exception:
                    pass  # keep original duration on failure
                logging.info(
                    f"Codec conversion complete for {self.filename} → {os.path.basename(playable_path)}"
                )
        except RuntimeError as e:
            logging.error(f"Codec conversion failed for {self.filename}: {e}")
            raise
    
    def cleanup(self):
        """remove files and reset playback state"""
        # remove converted file - always temporary
        if self.converted_path and os.path.exists(self.converted_path):
            try:
                os.remove(self.converted_path)
                logging.info(f"Cleaned up converted: {os.path.basename(self.converted_path)}")
            except Exception as e:
                logging.error(f"Cleanup failed for converted {self.converted_path}: {e}")
        self.converted_path = None
        
        # remove original downloaded file if not permanent
        if not self.is_permanent and self.downloaded_path and os.path.exists(self.downloaded_path):
            try:
                os.remove(self.downloaded_path)
                logging.info(f"Cleaned up: {self.filename}")
            except Exception as e:
                logging.error(f"Cleanup failed for {self.downloaded_path}: {e}")
        
        self.downloaded_path = None
        self.position = 0
        self.playback_start_time = None
        self.paused_position = 0
        self.is_paused = False
    
    def start_playback(self, position: float = None):
        """mark track as playing"""
        if position is not None:
            self.position = position
        self.playback_start_time = time.time() - self.position
        self.is_paused = False
    
    def pause_playback(self):
        """pause and store current position"""
        if not self.is_paused and self.playback_start_time:
            self.paused_position = time.time() - self.playback_start_time
            self.is_paused = True
    
    def resume_playback(self):
        """resume from paused position"""
        if self.is_paused:
            self.playback_start_time = time.time() - self.paused_position
            self.is_paused = False
    
    def get_current_position(self) -> float:
        """get current playback position in seconds"""
        if not self.playback_start_time:
            return self.position
        if self.is_paused:
            return self.paused_position
        return min(time.time() - self.playback_start_time, self.duration)
    
    def to_dict(self) -> dict:
        """convert track to display dictionary"""
        return {
            'filename': self.filename,
            'requester': self.requester,
            'duration': str(timedelta(seconds=int(self.duration))),
            'position': str(timedelta(seconds=int(self.get_current_position()))),
            'size_mb': self.file_size / (1024 * 1024),
            'bitrate': f"{self.bitrate}kbps"
        }


@dataclass
class GuildState:
    """stores playback state for single guild"""
    queue: List[AudioTrack] = field(default_factory=list)
    queue_position: int = -1
    volume: int = 100
    last_activity: float = field(default_factory=time.time)
    last_channel_id: Optional[int] = None
    
    # loop settings
    loop_enabled: bool = False
    loop_count: int = 0
    max_loops: Optional[int] = None
    
    # playback control flags
    is_seeking: bool = False
    manual_queue_seek: bool = False
    is_stopped: bool = False
    
    # ui state - now playing message tracking
    last_np_message: object = None  # discord.Message reference
    last_np_track: object = None    # AudioTrack for that message
    
    @property
    def current_track(self) -> Optional[AudioTrack]:
        """get current track based on queue position"""
        if 0 <= self.queue_position < len(self.queue):
            return self.queue[self.queue_position]
        return None
    
    def reset(self):
        """reset playback state, keep volume setting"""
        self.queue.clear()
        self.queue_position = -1
        self.loop_enabled = False
        self.loop_count = 0
        self.max_loops = None
        self.is_seeking = False
        self.manual_queue_seek = False
        self.is_stopped = False
        self.last_np_message = None
        self.last_np_track = None


class MusicState:
    """manages music state across all guilds"""
    def __init__(self):
        self.guild_states: Dict[int, GuildState] = {}
        self.alone_since: Dict[int, float] = {}
        self.rate_limits: Dict[int, float] = {}
    
    async def get_guild_state(self, guild_id: int) -> GuildState:
        """get or create guild state and update activity"""
        if guild_id not in self.guild_states:
            self.guild_states[guild_id] = GuildState()
        self.guild_states[guild_id].last_activity = time.time()
        return self.guild_states[guild_id]
    
    def check_rate_limit(self, guild_id: int, cooldown: float = 2.0) -> bool:
        """check if guild is rate limited, returns true if allowed"""
        now = time.time()
        if guild_id in self.rate_limits and now - self.rate_limits[guild_id] < cooldown:
            return False
        self.rate_limits[guild_id] = now
        return True
