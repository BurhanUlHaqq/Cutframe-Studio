"""Local YouTube download via yt-dlp.

Returns a local mp4 path so the rest of the local pipeline can read it
directly off disk.
"""
import os
import re
from pathlib import Path
from shutil import which
from urllib.parse import parse_qs, urlparse
from typing import Callable, Optional

from ..url_validation import validate_youtube_url
from .sections import extract_section


def _import_ytdlp():
    try:
        import yt_dlp  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "yt-dlp is required to download YouTube videos. Install it with:\n"
            "    pip install -r requirements.txt"
        ) from e
    return yt_dlp


def _format_for(fmt: str) -> str:
    """Map our '720' / '1080' shorthand to a yt-dlp format selector."""
    try:
        height = int(fmt)
    except ValueError:
        height = 720
    return (
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"best[height<={height}][ext=mp4]/best"
    )


def _extract_youtube_video_id(source: str) -> Optional[str]:
    """Best-effort extraction of a YouTube video id from a URL."""
    parsed = urlparse(source)
    host = (parsed.hostname or "").lower()

    if host == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/", 1)[0]
        return video_id or None

    if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
        if parsed.path.startswith("/watch"):
            qs = parse_qs(parsed.query)
            video_id = qs.get("v", [""])[0]
            return video_id or None
        match = re.search(r"/(?:shorts|embed|live)/([^/?#&]+)", parsed.path)
        if match:
            return match.group(1)

    return None


def cached_download(out_dir, video_id: str) -> Optional[Path]:
    """Return a cached download path if we already have this YouTube id."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return None
    for ext in (".mp4", ".mkv", ".webm"):
        candidate = Path(out_dir) / f"source_{video_id}{ext}"
        try:
            if not candidate.is_symlink() and candidate.is_file() and candidate.stat().st_size:
                return candidate
        except OSError:
            continue
    return None


def cached_youtube_source(video_url, out_dir):
    return cached_download(out_dir, _extract_youtube_video_id(video_url) or "")


def download_youtube_section(video_url, *, out_dir, fmt, source_range, on_progress=None):
    video_url = validate_youtube_url(video_url)
    video_id = _extract_youtube_video_id(video_url)
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        raise ValueError("Choose an individual YouTube video.")
    start_ms, end_ms = source_range
    cache = Path(out_dir) / f"section-{video_id}-{fmt}-{start_ms}-{end_ms}.mp4"
    if cache.is_file() and cache.stat().st_size > 0:
        return str(cache), start_ms / 1000, end_ms / 1000
    _, start, end = extract_section(video_url, cache, source_range, extractors=("youtube",), quality=fmt,
                                    on_progress=on_progress)
    return str(cache), start, end


def download_youtube_local(
    video_url: str, *, out_dir: str, fmt: str = "720",
    on_bytes: Optional[Callable[[int, int], None]] = None,
    on_cache: Optional[Callable[[], None]] = None,
) -> str:
    """Download a YouTube video to the job directory or reuse its cached copy."""
    video_url = validate_youtube_url(video_url)

    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    video_id = _extract_youtube_video_id(video_url)
    if video_id:
        cached = cached_download(out_dir, video_id)
        if cached:
            print(f"[download/local] reusing cached download: {cached}", flush=True)
            if on_cache is not None:
                on_cache()
            return str(cached)

    yt_dlp = _import_ytdlp()
    print(f"[download/local] {video_url} @ {fmt}p → {out_dir}/", flush=True)
    ydl_opts = {
        "format": _format_for(fmt),
        "outtmpl": os.path.join(out_dir, "source_%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
    }
    if which("node"):
        ydl_opts["js_runtimes"] = {"deno": {}, "node": {}}
    if on_bytes is not None:
        def download_progress(status):
            if status.get("status") == "downloading":
                on_bytes(status.get("downloaded_bytes") or 0,
                         status.get("total_bytes") or status.get("total_bytes_estimate") or 0)
        ydl_opts["progress_hooks"] = [download_progress]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        path = ydl.prepare_filename(info)
        # merge_output_format may rename the extension after merge
        if not os.path.exists(path):
            stem, _ = os.path.splitext(path)
            for ext in (".mp4", ".mkv", ".webm"):
                if os.path.exists(stem + ext):
                    path = stem + ext
                    break

    print(f"[download/local] ready: {path}", flush=True)
    return path
