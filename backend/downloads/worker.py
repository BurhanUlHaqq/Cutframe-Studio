"""Fetch and verify original videos without going through the render pipeline."""

import json
import logging
import math
import shutil
import subprocess
import time
from itertools import islice

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from filelock import FileLock

from shorts_generator.local.network import PlatformDownloader
from shorts_generator.local.media import InvalidSourceVideo, inspect_local_video
from shorts_generator.local.sections import extract_section, video_within_8k
from yt_dlp.utils import DownloadError

from .models import Download
from .sources import FILE_LIMIT, PLAYLIST_LIMIT, PLAYLISTS, SOURCES, normalize_source


LOG = logging.getLogger(__name__)
Status = Download.Status
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".mpeg", ".ts"}
ACTIVE = (Status.QUEUED, Status.RUNNING, Status.CANCELLING)
TERMINAL = (Status.COMPLETE, Status.PARTIAL, Status.FAILED, Status.CANCELLED)
FILE_SIZE_MESSAGE = f"This item exceeds the {FILE_LIMIT // 1024**3} GiB limit. Try a lower quality."


class StopDownload(Exception):
    pass


class DownloadLimit(Exception):
    pass


class QuietLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


def download_directory(download_id):
    return settings.STUDIO_DATA_DIR / "downloads" / str(download_id)


def asset_directory(job):
    if job.parent_id:
        return download_directory(job.parent_id) / f"item-{job.position}"
    return download_directory(job.pk)


def asset_path(job):
    suffix = f".{job.extension}"
    allowed = {".mp3"} if job.output == "mp3" else VIDEO_EXTENSIONS
    if suffix not in allowed:
        raise FileNotFoundError
    path = asset_directory(job) / f"video{suffix}"
    if path.is_symlink():
        raise FileNotFoundError
    return path


def remove_files(job):
    directory = asset_directory(job)
    if directory.exists():
        shutil.rmtree(directory)


def update(queue, **fields):
    return queue.update(**fields, updated_at=timezone.now())


class Progress:
    def __init__(self, job, prefix=""):
        self.queue = Download.objects.filter(pk=job.pk, status=Status.RUNNING)
        self.parent = Download.objects.filter(pk=job.parent_id, status=Status.RUNNING) if job.parent_id else None
        self.prefix = prefix
        self.audio_only = job.output == "mp3"
        self.parts = {}
        self.checked_at = 0.0

    def report(self, **fields):
        if self.parent is not None:
            if fields.get("status") == Status.COMPLETE:
                active = self.parent.exists()
            else:
                current = {key: fields[key] for key in ("bytes_received", "bytes_expected") if key in fields}
                if fields.get("activity"):
                    current["activity"] = self.prefix + fields["activity"]
                active = update(self.parent, **current)
            if not active:
                raise StopDownload
        if not update(self.queue, **fields):
            raise StopDownload

    def transfer(self, data):
        if data.get("status") not in ("downloading", "finished"):
            return
        received = data.get("downloaded_bytes") or 0
        total = max(received, data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
        self.parts[data.get("filename", "")] = (received, total)
        transferred = sum(part[0] for part in self.parts.values())
        if transferred > FILE_LIMIT:
            raise DownloadLimit
        now = time.monotonic()
        if now - self.checked_at < 0.5 and data["status"] == "downloading":
            return
        self.checked_at = now
        expected = sum(part[1] for part in self.parts.values()) if data.get("total_bytes") or data.get("total_bytes_estimate") else 0
        activity = "Downloading audio" if self.audio_only else "Downloading video streams"
        self.report(bytes_received=transferred, bytes_expected=expected, activity=activity)

    def processing(self, data):
        if data.get("status") == "started":
            activity = "Converting audio to MP3" if self.audio_only else "Combining video and audio"
            self.report(activity=activity)


def video_filter(info, *, incomplete=False):
    if info.get("live_status") in {"is_live", "is_upcoming", "post_live"} or info.get("is_live"):
        return "Use a finished video or replay."
    return None


def provider_options():
    options = {
        "quiet": True, "noprogress": True, "no_warnings": True, "logger": QuietLogger(),
        "socket_timeout": 18, "retries": 2, "fragment_retries": 2,
        "skip_unavailable_fragments": False, "hls_prefer_native": True,
        # Keep external processes on local media files, including yt-dlp's optional HLS fallback.
        "external_downloader_args": {"ffmpeg_i": ["-protocol_whitelist", "pipe"]},
        "postprocessor_args": {"ffmpeg_i": ["-protocol_whitelist", "file,pipe"]},
        "enable_file_urls": False, "proxy": "",
    }
    if shutil.which("node"):
        options["js_runtimes"] = {"node": {}}
    return options


def fetch_video(job, directory, progress):
    protocol = "[protocol~='^(https?|m3u8_native|http_dash_segments)$']"
    audio_only = getattr(job, "output", "video") == "mp3"
    if getattr(job, "source_start_ms", None) is not None:
        progress.report(activity="Finding the selected section")
        output = directory / ("video.mp3" if audio_only else "video.mp4")

        def report_section(received, portion):
            if received > FILE_LIMIT:
                raise DownloadLimit
            progress.report(bytes_received=received, bytes_expected=0,
                            activity=f"Downloading selected section · {int(portion * 100)}%")

        info, _, _ = extract_section(
            job.source_url, output, (job.source_start_ms, job.source_end_ms), extractors=SOURCES[job.platform][1],
            quality=job.quality, audio_bitrate=job.audio_bitrate if audio_only else None, on_progress=report_section,
        )
        return info, output
    selector = f"ba{protocol}/b{protocol}" if audio_only else f"bv*{protocol}+ba{protocol}/b{protocol}/bv*{protocol}"
    resolution = "res" if job.quality == "best" else f"res:{job.quality}"
    options = {
        **provider_options(),
        "format": selector, "format_sort_force": True,
        "format_sort": ["abr", "asr"] if audio_only else [resolution, "vcodec:h264", "acodec:aac"],
        "merge_output_format": "mp4/mkv", "outtmpl": str(directory / "video.%(ext)s"),
        "noplaylist": True, "playlistend": 1, "match_filter": video_filter, "max_filesize": FILE_LIMIT,
        "progress_hooks": [progress.transfer], "postprocessor_hooks": [progress.processing],
    }
    if audio_only:
        options["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(job.audio_bitrate)}
        ]
    with PlatformDownloader(options, SOURCES[job.platform][1]) as client:
        if not audio_only:
            select = client.build_format_selector(selector)

            def select_within_limit(context):
                formats = [fmt for fmt in context["formats"] if video_within_8k(fmt)]
                return select({**context, "formats": formats})

            client.params["format"] = select_within_limit
        info = client.extract_info(job.source_url, download=True)
    if not isinstance(info, dict) or "entries" in info or video_filter(info):
        raise DownloadError("Use a finished individual video or replay.")
    allowed = {".mp3"} if audio_only else VIDEO_EXTENSIONS
    for path in directory.glob("video.*"):
        if path.suffix in allowed and not path.is_symlink() and path.is_file() and path.stat().st_size > 0:
            return info, path
    raise DownloadError("No downloadable media file was returned.")


def probe_download(path, *, audio_only):
    if not audio_only:
        duration, _, video = inspect_local_video(path, require_audio=False)
        if not video_within_8k(video):
            raise InvalidSourceVideo("The source video exceeds 8K.")
        return duration, video.get("width"), video.get("height")
    command = [
        "ffprobe", "-v", "error", "-protocol_whitelist", "file", "-format_whitelist", "mp3",
        "-select_streams", "a:0", "-show_entries", "stream=codec_name:format=duration", "-of", "json", str(path),
    ]
    try:
        info = json.loads(subprocess.check_output(command, stderr=subprocess.PIPE, timeout=30))
        duration = float(info["format"]["duration"])
        if info["streams"][0]["codec_name"] == "mp3" and math.isfinite(duration) and duration > 0:
            return duration, None, None
    except (subprocess.SubprocessError, KeyError, IndexError, ValueError, TypeError):
        pass
    raise InvalidSourceVideo("The resulting MP3 could not be played.")


def media_title(value, fallback):
    return " ".join(str(value or "").split())[:240] or fallback


def discover_playlist(job):
    end = job.playlist_end or (job.playlist_start + PLAYLIST_LIMIT)
    options = {
        **provider_options(),
        "extract_flat": "in_playlist", "lazy_playlist": True, "skip_download": True,
        "playlist_items": f"{job.playlist_start}:{end}",
    }
    extractors = (*PLAYLISTS[job.platform], *SOURCES[job.platform][1])
    with PlatformDownloader(options, extractors) as client:
        info = client.extract_info(job.source_url, download=False)
        if not isinstance(info, dict) or info.get("entries") is None:
            raise DownloadError("Use a public playlist link.")
        entries = list(islice(info["entries"], PLAYLIST_LIMIT + 1))
    if not entries:
        raise DownloadError("There are no available entries in this playlist range.")
    items = []
    for position, entry in enumerate(entries[:PLAYLIST_LIMIT], start=job.playlist_start):
        entry = entry if isinstance(entry, dict) else {}
        try:
            url, platform = normalize_source(entry.get("url"))
            if platform != job.platform:
                raise ValueError
            status, activity = Status.QUEUED, "Waiting for playlist item."
        except ValueError:
            url = ""
            status, activity = Status.FAILED, "The platform did not provide a playable entry."
        items.append(Download(
            owner=job.owner, parent=job, position=position, source_url=url, platform=job.platform,
            quality=job.quality, output=job.output, audio_bitrate=job.audio_bitrate, status=status,
            title=media_title(entry.get("title"), f"Item {position}"), activity=activity,
        ))
    with transaction.atomic():
        parent = Download.objects.select_for_update().filter(pk=job.pk, status=Status.RUNNING).first()
        if not parent:
            raise StopDownload
        Download.objects.bulk_create(items)
        parent.title = media_title(info.get("title"), "Playlist")
        parent.playlist_limited = len(entries) > PLAYLIST_LIMIT
        parent.save(update_fields=["title", "playlist_limited", "updated_at"])


def failure_message(exc):
    if isinstance(exc, DownloadLimit):
        return FILE_SIZE_MESSAGE
    if isinstance(exc, InvalidSourceVideo):
        return str(exc)
    message = str(exc).lower()
    if "live" in message or "finished" in message:
        return "Live streams are available here once the replay has finished."
    if any(term in message for term in ("403", "429", "sign in", "log in", "login", "logged-in", "private")):
        return "The source restricted this request. Check that the video is public and available in your region."
    if any(term in message for term in ("not found", "unavailable", "deleted")):
        return "The source video is unavailable. Open the link to check that it still plays."
    if "max-filesize" in message:
        return FILE_SIZE_MESSAGE
    if "playlist" in message:
        return "No items could be loaded. Check that the playlist is public and the requested range exists."
    if "unsupported url" in message or "individual video" in message:
        return "Open the original video on a supported platform and copy its share link."
    if "disk" in message or "space left" in message:
        return "The download could not be saved. Check free disk space."
    return "A media file could not be downloaded. Check the link and try again."


def discard_incomplete(job):
    try:
        remove_files(job)
    except OSError:
        LOG.warning("Could not remove incomplete download files for %s.", job.pk)


def finish_cancelled(job):
    queue = Download.objects.filter(pk=job.pk, status__in=(Status.CANCELLING, Status.RUNNING))
    if job.is_playlist:
        active = job.items.filter(status__in=ACTIVE)
        for item in active:
            discard_incomplete(item)
        update(active, status=Status.CANCELLED, bytes_received=0, bytes_expected=0, activity="Download stopped.")
    else:
        discard_incomplete(job)
    update(queue, status=Status.CANCELLED, bytes_received=0, bytes_expected=0, activity="Download stopped.")


def run_media(job, prefix=""):
    progress = Progress(job, prefix)
    directory = asset_directory(job)
    try:
        remove_files(job)
        directory.mkdir(parents=True)
        info, path = fetch_video(job, directory, progress)
        progress.report(activity="Checking the finished file")
        size = path.stat().st_size
        if size > FILE_LIMIT:
            raise DownloadLimit
        audio_only = job.output == "mp3"
        duration, width, height = probe_download(path, audio_only=audio_only)
        title = media_title(info.get("title"), job.title or "Video")
        progress.report(status=Status.COMPLETE, title=title, extension=path.suffix[1:], duration=duration,
                        width=width, height=height,
                        bytes_received=size, bytes_expected=size, activity="Ready to save")
    except StopDownload:
        finish_cancelled(job)
        if job.parent_id:
            raise
    except Exception as exc:
        if progress.parent is not None and not progress.parent.exists():
            finish_cancelled(job)
            raise StopDownload from exc
        discard_incomplete(job)
        queue = Download.objects.filter(pk=job.pk)
        if update(queue.filter(status=Status.RUNNING), status=Status.FAILED, activity=failure_message(exc),
                  bytes_received=0, bytes_expected=0):
            LOG.warning("Download %s failed (%s)", job.pk, type(exc).__name__)
        else:
            finish_cancelled(job)


def run_playlist(job):
    running = Download.objects.filter(pk=job.pk, status=Status.RUNNING)
    try:
        if not job.items.exists():
            discover_playlist(job)
        for item in job.items.filter(status=Status.QUEUED).order_by("position").iterator():
            prefix = f"Item {item.position} · "
            if not update(running, activity=prefix + "Connecting to the source", bytes_received=0, bytes_expected=0):
                raise StopDownload
            update(Download.objects.filter(pk=item.pk), status=Status.RUNNING)
            run_media(item, prefix)
        total = job.items.count()
        ready = job.items.filter(status=Status.COMPLETE).count()
        status = Status.COMPLETE if ready == total else Status.PARTIAL if ready else Status.FAILED
        if not update(running, status=status, bytes_received=0, bytes_expected=0,
                      activity=f"{ready} of {total} playlist items ready."):
            raise StopDownload
    except StopDownload:
        finish_cancelled(job)
    except Exception as exc:
        LOG.warning("Playlist %s failed (%s)", job.pk, type(exc).__name__)
        status = Status.PARTIAL if job.items.filter(status=Status.COMPLETE).exists() else Status.FAILED
        if not update(running, status=status, activity=failure_message(exc), bytes_received=0, bytes_expected=0):
            finish_cancelled(job)


def process_next():
    job = Download.objects.filter(parent__isnull=True, status=Status.QUEUED).order_by("created_at", "pk").first()
    if job is None:
        return False
    queued = Download.objects.filter(pk=job.pk, status=Status.QUEUED)
    activity = "Loading playlist items" if job.is_playlist else "Connecting to the source"
    if update(queued, status=Status.RUNNING, activity=activity):
        if job.is_playlist:
            run_playlist(job)
        else:
            run_media(job)
    return True


def run_queue(*, once=False):
    root = settings.STUDIO_DATA_DIR / "downloads"
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(root / "queue.lock", timeout=0):
        for job in Download.objects.filter(parent__isnull=True, status=Status.CANCELLING).iterator():
            finish_cancelled(job)
        update(Download.objects.filter(status=Status.RUNNING), status=Status.QUEUED,
               activity="Waiting to restart after interruption", bytes_received=0, bytes_expected=0)
        while True:
            worked = process_next()
            if once:
                return
            if not worked:
                time.sleep(1)
