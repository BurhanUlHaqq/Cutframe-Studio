"""Inspect a supplied video before the worker renders it."""

import json
import math
import os
import subprocess

from ..timestamps import source_timestamp


FORMATS = "mov,matroska,avi,mpeg,mpegts"


class InvalidSourceVideo(ValueError):
    pass


def inspect_local_video(path, *, require_audio=True):
    if not path.is_file():
        raise InvalidSourceVideo("This upload is unavailable. Start a new render and choose the video again.")
    command = [
        "ffprobe", "-v", "error", "-protocol_whitelist", "file", "-format_whitelist", FORMATS,
        "-show_entries", "stream=codec_type,index,width,height:stream_disposition=attached_pic:format=duration",
        "-of", "json", str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=True, timeout=30)
        probe = json.loads(result.stdout)
        duration = float(probe["format"]["duration"])
    except (subprocess.SubprocessError, ValueError, TypeError, KeyError) as exc:
        raise InvalidSourceVideo("The uploaded file could not be read as a video. Try another video file.") from exc
    streams = probe.get("streams", [])
    video = next((stream for stream in streams
                  if stream["codec_type"] == "video" and not stream.get("disposition", {}).get("attached_pic")), None)
    if not math.isfinite(duration) or duration <= 0 or video is None:
        raise InvalidSourceVideo("Choose a video with a playable picture.")
    has_audio = any(stream["codec_type"] == "audio" for stream in streams)
    if require_audio and not has_audio:
        raise InvalidSourceVideo("Choose a video with an audio track for transcription.")
    return duration, has_audio, video


def validate_local_video(path, *, require_audio=True):
    duration, has_audio, video = inspect_local_video(path, require_audio=require_audio)
    return duration, has_audio, video["index"]


def resolve_source_range(duration, start_ms, end_ms):
    start, end = start_ms / 1000, end_ms / 1000
    if start >= duration or end > duration + 0.001:
        limit = source_timestamp(round(duration * 1000))
        raise InvalidSourceVideo(f"The selected timestamps exceed the source duration of {limit}. Choose a range inside the source.")
    return start, min(end, duration)


def cut_subclip(source_path: str, start: float, end: float, out_path: str) -> str:
    """Seek to the requested start, then re-encode an accurate cut with audio."""
    source_path = os.path.abspath(os.path.expanduser(source_path))
    out_path = os.path.abspath(os.path.expanduser(out_path))
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}",
        "-protocol_whitelist", "file", "-i", source_path,
        "-t", f"{end - start:.3f}",
        "-map", "0:V:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ]
    subprocess.run(cmd, check=True)
    return out_path
