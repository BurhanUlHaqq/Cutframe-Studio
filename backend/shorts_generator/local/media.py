"""Inspect a supplied video before the worker renders it."""

import json
import math
import subprocess


FORMATS = "mov,matroska,avi,mpeg,mpegts"


class InvalidSourceVideo(ValueError):
    pass


def validate_local_video(path, *, require_audio=True):
    if not path.is_file():
        raise InvalidSourceVideo("This upload is unavailable. Start a new render and choose the video again.")
    command = [
        "ffprobe", "-v", "error", "-protocol_whitelist", "file", "-format_whitelist", FORMATS,
        "-show_entries", "stream=codec_type,index:stream_disposition=attached_pic:format=duration",
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
    return duration, has_audio, video["index"]
