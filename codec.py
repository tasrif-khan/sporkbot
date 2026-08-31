"""
codec detection and conversion for SporkMP3.
transcodes incompatible codecs (xHE-AAC, USAC) to discord.py format.

strategy: try libfdk_aac decoder first, then native with -strict experimental, validate output.
"""
import asyncio
import json
import logging
import os
import struct
from typing import Optional, Tuple


# native codecs - discord.py handles these directly
NATIVE_CODECS = {
    'pcm_s16le', 'pcm_s16be', 'pcm_s24le', 'pcm_s24be',
    'pcm_s32le', 'pcm_s32be', 'pcm_f32le', 'pcm_f64le',
    'flac', 'alac', 'wavpack',
    'mp3', 'mp2', 'aac', 'vorbis', 'opus',
    'wmav1', 'wmav2', 'adpcm_ms', 'adpcm_ima_wav',
}

# codecs requiring conversion - known problematic
CONVERT_CODECS = {
    'usac',          # xHE-AAC main target
    'als',           # ALS lossless rare
    'sls',           # SLS scalable lossless
}

# supported input file extensions
SUPPORTED_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.mp4', '.webm'}

# cache decoder availability per process
_decoder_cache: dict = {}


# decoder availability checks
async def _check_decoder(decoder_name: str) -> bool:
    """check if specific FFmpeg decoder is available"""
    if decoder_name in _decoder_cache:
        return _decoder_cache[decoder_name]

    try:
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg', '-hide_banner', '-decoders',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        available = decoder_name in stdout.decode(errors='replace')
        _decoder_cache[decoder_name] = available
        logging.info(f"Decoder '{decoder_name}' available: {available}")
        return available
    except Exception as e:
        logging.warning(f"Could not check decoder availability: {e}")
        _decoder_cache[decoder_name] = False
        return False


async def has_fdk_aac() -> bool:
    """check if libfdk_aac is available for best quality"""
    return await _check_decoder('libfdk_aac')


# probing - detect codec and extract metadata
async def probe_codec(file_path: str) -> Optional[str]:
    """use ffprobe to detect audio codec name"""
    try:
        proc = await asyncio.create_subprocess_exec(
            'ffprobe',
            '-v', 'quiet',
            '-select_streams', 'a:0',
            '-show_entries', 'stream=codec_name',
            '-of', 'json',
            file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        if proc.returncode != 0:
            logging.warning(f"ffprobe failed for {file_path}: {stderr.decode(errors='replace')}")
            return None

        data = json.loads(stdout.decode())
        streams = data.get('streams', [])
        if streams:
            codec = streams[0].get('codec_name', '').lower()
            logging.debug(f"Probed codec for {file_path}: {codec}")
            return codec
        return None

    except asyncio.TimeoutError:
        logging.error(f"ffprobe timed out for {file_path}")
        return None
    except Exception as e:
        logging.error(f"ffprobe error for {file_path}: {e}")
        return None


async def probe_format_details(file_path: str) -> dict:
    """get extended probe info - codec, sample rate, channels, bitrate, duration"""
    try:
        proc = await asyncio.create_subprocess_exec(
            'ffprobe',
            '-v', 'quiet',
            '-select_streams', 'a:0',
            '-show_entries', 'stream=codec_name,sample_rate,channels,bit_rate,codec_long_name,profile',
            '-show_entries', 'format=duration,format_name',
            '-of', 'json',
            file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)

        if proc.returncode != 0:
            return {}

        data = json.loads(stdout.decode())
        result = {}

        streams = data.get('streams', [])
        if streams:
            s = streams[0]
            result['codec'] = s.get('codec_name', '').lower()
            result['codec_long'] = s.get('codec_long_name', '')
            result['sample_rate'] = int(s.get('sample_rate', 0))
            result['channels'] = int(s.get('channels', 0))
            result['bit_rate'] = int(s.get('bit_rate', 0)) if s.get('bit_rate') else None
            result['profile'] = s.get('profile', '').strip()

        fmt = data.get('format', {})
        result['duration'] = float(fmt.get('duration', 0))
        result['format_name'] = fmt.get('format_name', '')

        return result

    except Exception as e:
        logging.error(f"Detailed probe failed for {file_path}: {e}")
        return {}


# validation - check converted output for issues
async def _detect_mean_volume(file_path: str) -> Optional[float]:
    """use ffmpeg volumedetect to get mean volume in dB"""
    try:
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg',
            '-i', file_path,
            '-af', 'volumedetect',
            '-f', 'null', '-',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stderr.decode(errors='replace')

        for line in output.splitlines():
            if 'mean_volume' in line:
                parts = line.split('mean_volume:')
                if len(parts) == 2:
                    vol_str = parts[1].strip().replace('dB', '').strip()
                    if vol_str == '-inf':
                        return -999.0
                    return float(vol_str)
        return None
    except Exception as e:
        logging.warning(f"Volume detection failed for {file_path}: {e}")
        return None


async def _validate_conversion(
    input_path: str,
    output_path: str,
    source_duration: float,
) -> Tuple[bool, str]:
    """validate converted output isn't corrupt, silent, or truncated"""
    # check file exists and not empty
    if not os.path.exists(output_path):
        return False, "Output file does not exist"
    if os.path.getsize(output_path) == 0:
        return False, "Output file is empty (0 bytes)"

    # duration sanity check - allow 2s or 10% drift
    out_details = await probe_format_details(output_path)
    out_duration = out_details.get('duration', 0)

    if source_duration > 0 and out_duration > 0:
        drift = abs(out_duration - source_duration)
        pct = drift / source_duration
        if drift > 2.0 and pct > 0.10:
            return False, (
                f"Duration mismatch: source={source_duration:.1f}s, "
                f"output={out_duration:.1f}s (drift={pct*100:.1f}%)"
            )

    # silence detection - mean volume check
    mean_vol = await _detect_mean_volume(output_path)
    if mean_vol is not None:
        if mean_vol < -90:
            return False, f"Output is silent (mean_volume={mean_vol:.1f} dB)"

    return True, "Valid"


def _is_xhe_aac_file(file_path: str) -> bool:
    """check mp4 for xHE-AAC (USAC AOT 42) audio object type"""
    try:
        with open(file_path, 'rb') as f:
            # minimal mp4 parsing - find ftyp and moov atoms
            f.seek(0)
            data = f.read(1024)
            if b'ftyp' not in data:
                return False
            
            f.seek(0)
            while True:
                size_bytes = f.read(4)
                if len(size_bytes) < 4:
                    break
                
                box_type = f.read(4)
                if len(box_type) < 4:
                    break
                
                size = struct.unpack('>I', size_bytes)[0]
                if size < 8:
                    break
                
                if box_type == b'moov':
                    # search for USAC audio object type in moov
                    moov_data = f.read(size - 8)
                    if b'esds' in moov_data:
                        esds_idx = moov_data.find(b'esds')
                        if esds_idx >= 20:
                            aot_byte = moov_data[esds_idx + 20]
                            if aot_byte == 42:  # xHE-AAC
                                return True
                    break
                
                f.seek(size - 8, 1)
        
        return False
    except Exception as e:
        logging.debug(f"xHE-AAC detection failed: {e}")
        return False


def needs_conversion(codec_name: Optional[str]) -> bool:
    """check if codec needs conversion for discord compatibility"""
    if codec_name is None:
        return True

    codec = codec_name.lower().strip()

    if codec in NATIVE_CODECS:
        return False
    if codec in CONVERT_CODECS:
        return True

    logging.info(f"Unknown codec '{codec}' converting to ensure compatibility")
    return True


# conversion - transcode audio files
async def _run_ffmpeg_convert(
    input_path: str,
    output_path: str,
    decoder: Optional[str],
    target_codec: str,
    bitrate: str,
    target_format: str,
) -> Tuple[bool, str]:
    """run single FFmpeg conversion attempt with specified decoder"""
    cmd = ['ffmpeg', '-y', '-hide_banner']

    # force specific decoder if specified
    if decoder:
        cmd += ['-c:a', decoder]

    # allow experimental decoders for native USAC
    cmd += ['-strict', 'experimental']

    cmd += [
        '-i', input_path,
        '-vn',                   # no video
        '-c:a', target_codec,    # output codec
        '-b:a', bitrate,         # output bitrate
        '-ar', '48000',          # discord standard sample rate
        '-ac', '2',              # stereo
        '-f', target_format,     # output container
        output_path,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        stderr_text = stderr.decode(errors='replace')

        if proc.returncode != 0:
            return False, stderr_text[-500:]
        return True, stderr_text[-500:]

    except asyncio.TimeoutError:
        return False, "FFmpeg timed out"
    except Exception as e:
        return False, str(e)


async def convert_to_compatible(
    input_path: str,
    output_dir: str,
    source_codec: Optional[str] = None,
    source_duration: float = 0,
    target_format: str = 'ogg',
    target_codec: str = 'libopus',
    bitrate: str = '192k',
) -> Tuple[Optional[str], Optional[float]]:
    """
    transcode audio to discord.py compatible format.
    
    multi-strategy: tries libfdk_aac first for xHE-AAC, falls back to native decoder.
    validates output for silence, truncation, or corruption.
    """
    base = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{base}_converted.{target_format}")

    # use conversion cache if output exists and validates
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        valid, reason = await _validate_conversion(input_path, output_path, source_duration)
        if valid:
            logging.info(f"Conversion cache hit (valid): {output_path}")
            details = await probe_format_details(output_path)
            return output_path, details.get('duration')
        else:
            logging.warning(f"Cached conversion invalid ({reason}), re-converting")
            os.remove(output_path)

    # determine if xHE-AAC - worth trying fdk first
    is_usac = source_codec and source_codec.lower() in ('usac', 'aac')

    # build ordered decoder strategies to try
    strategies = []
    if is_usac and await has_fdk_aac():
        strategies.append(('libfdk_aac', 'libfdk_aac decoder (best quality)'))
    strategies.append((None, 'native decoder (-strict experimental)'))

    last_error = ""
    for decoder, label in strategies:
        logging.info(f"Conversion attempt: {label} for {os.path.basename(input_path)}")

        # cleanup partial output from previous attempt
        if os.path.exists(output_path):
            os.remove(output_path)

        success, stderr_tail = await _run_ffmpeg_convert(
            input_path, output_path,
            decoder=decoder,
            target_codec=target_codec,
            bitrate=bitrate,
            target_format=target_format,
        )

        if not success:
            logging.warning(f"Strategy '{label}' failed: {stderr_tail[:200]}")
            last_error = stderr_tail
            continue

        # validate the output
        valid, reason = await _validate_conversion(input_path, output_path, source_duration)
        if not valid:
            logging.warning(f"Strategy '{label}' produced invalid output: {reason}")
            last_error = reason
            if os.path.exists(output_path):
                os.remove(output_path)
            continue

        # success - log and return
        details = await probe_format_details(output_path)
        duration = details.get('duration')
        file_size = os.path.getsize(output_path)
        logging.info(
            f"Conversion successful ({label}): "
            f"{output_path} ({file_size} bytes, {duration:.1f}s)"
        )
        return output_path, duration

    # all strategies failed
    logging.error(f"All conversion strategies failed for {input_path}: {last_error}")
    if os.path.exists(output_path):
        os.remove(output_path)
    return None, None


# high-level api - probe and convert if needed
async def ensure_playable(file_path: str, temp_folder: str) -> Tuple[str, bool]:
    """
    high-level helper: probe file and convert if necessary.
    
    returns: (playable_path, was_converted)
    native codec: returns original path unchanged
    needs conversion: returns converted path or raises RuntimeError
    """
    codec = await probe_codec(file_path)
    logging.info(f"Codec probe: {file_path} → {codec or 'unknown'}")

    # xHE-AAC detection: ffprobe may report as plain aac
    # read audio object type from mp4 to catch usac (aot 42)
    force_convert = False
    if codec == 'aac' and _is_xhe_aac_file(file_path):
        logging.warning(
            f"xHE-AAC (USAC) detected in {file_path} forcing conversion"
        )
        force_convert = True
        codec = 'usac'  # override for correct decoder selection

    # mp4/webm may have video or malformed metadata
    # always extract audio to clean file for these
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ('.mp4', '.webm') and not force_convert:
        logging.info(f"Video container ({ext}), forcing audio extraction: {os.path.basename(file_path)}")
        force_convert = True

    if not force_convert and not needs_conversion(codec):
        return file_path, False

    # get source details for validation and decoder selection
    details = await probe_format_details(file_path)
    codec_long = details.get('codec_long', codec or 'unknown')
    source_duration = details.get('duration', 0)

    logging.warning(
        f"Incompatible codec detected: {codec_long} in {file_path} converting"
    )

    converted_path, duration = await convert_to_compatible(
        file_path,
        temp_folder,
        source_codec=codec,
        source_duration=source_duration,
    )
    if converted_path is None:
        raise RuntimeError(
            f"Failed to convert {os.path.basename(file_path)} "
            f"(codec: {codec_long}). The file may be corrupted or "
            f"your FFmpeg build may not support this codec. "
            f"Consider installing FFmpeg with libfdk_aac support."
        )

    return converted_path, True
