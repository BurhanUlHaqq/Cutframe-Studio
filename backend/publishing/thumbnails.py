"""Resolve the approved cover for the publishing worker."""

import math
import re
from urllib.parse import parse_qs, urlsplit

from studio.exports import job_directory, thumbnail_filename

from .models import Platform


def youtube_video_id(url):
    parts = urlsplit(url)
    if (parts.scheme, parts.netloc, parts.path) != ("https", "www.youtube.com", "/watch"):
        return ""
    video = parse_qs(parts.query).get("v", [""])[0]
    return video if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", video) else ""


def thumbnail_path(job, filename):
    if not filename or not re.fullmatch(r"clip-\d+(?:-cover-[a-f0-9]{24})?\.jpg", filename):
        return None
    root = (job_directory(job.id) / "files").resolve()
    path = (root / filename).resolve()
    return path if path.is_relative_to(root) and path.is_file() and path.stat().st_size else None


def cover_options(job, index, platform):
    clip = job.clips[index]
    if platform == Platform.YOUTUBE:
        filename = thumbnail_filename(clip)
        available = bool(thumbnail_path(job, filename))
        return {"thumbnail_file": filename if available else "", "thumbnail_state": "queued" if available else "missing"}
    design = clip.get("thumbnail_design") or {}
    choices = clip.get("thumbnail_options") or []
    frame = design.get("frame")
    time = choices[frame].get("time") if type(frame) is int and 0 <= frame < len(choices) else None
    if isinstance(time, (int, float)) and 0 <= time <= 1800 and math.isfinite(time):
        return {"thumbnail_time_ms": round(time * 1000)}
    return {}
