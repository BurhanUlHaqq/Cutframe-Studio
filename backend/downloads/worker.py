"""Fetch and verify original videos without going through the render pipeline."""

import logging
import shutil
import time

from django.conf import settings
from django.utils import timezone
from filelock import FileLock

from shorts_generator.local.media import InvalidSourceVideo, validate_local_video
from yt_dlp.utils import DownloadError

from .models import Download
from .network import PlatformDownloader
from .sources import FILE_LIMIT, SOURCES


LOG = logging.getLogger(__name__)
Status = Download.Status
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".mpeg", ".ts"}
ACTIVE = (Status.QUEUED, Status.RUNNING, Status.CANCELLING)
TERMINAL = (Status.COMPLETE, Status.FAILED, Status.CANCELLED)


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


def remove_files(download_id):
    directory = download_directory(download_id)
    if directory.exists():
        shutil.rmtree(directory)


def update(queue, **fields):
    return queue.update(**fields, updated_at=timezone.now())


class Progress:
    def __init__(self, job_id):
        self.queue = Download.objects.filter(pk=job_id, status=Status.RUNNING)
        self.parts = {}
        self.checked_at = 0.0

    def report(self, **fields):
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
        self.report(bytes_received=transferred, bytes_expected=expected, activity="Downloading video streams")

    def processing(self, data):
        if data.get("status") == "started":
            self.report(activity="Combining video and audio")


def video_filter(info, *, incomplete=False):
    if info.get("live_status") in {"is_live", "is_upcoming", "post_live"} or info.get("is_live"):
        return "Use a finished video or replay."
    return None


def fetch_video(job, directory, progress):
    protocol = "[protocol~='^(https?|m3u8_native|http_dash_segments)$']"
    resolution = "res" if job.quality == "best" else f"res:{job.quality}"
    options = {
        "format": f"bv*{protocol}+ba{protocol}/b{protocol}/bv*{protocol}",
        "format_sort": [resolution, "vcodec:h264", "acodec:aac"],
        "merge_output_format": "mp4/mkv",
        "outtmpl": str(directory / "video.%(ext)s"),
        "noplaylist": True,
        "playlistend": 1,
        "match_filter": video_filter,
        "max_filesize": FILE_LIMIT,
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
        "logger": QuietLogger(),
        "socket_timeout": 18,
        "retries": 2,
        "fragment_retries": 2,
        "skip_unavailable_fragments": False,
        "hls_prefer_native": True,
        # Keep network reads on yt-dlp's public-only transport, even if native HLS cannot handle a stream.
        "external_downloader_args": {"ffmpeg_i": ["-protocol_whitelist", "pipe"]},
        "postprocessor_args": {"ffmpeg_i": ["-protocol_whitelist", "file,pipe"]},
        "enable_file_urls": False,
        "proxy": "",
        "progress_hooks": [progress.transfer],
        "postprocessor_hooks": [progress.processing],
    }
    if shutil.which("node"):
        options["js_runtimes"] = {"node": {}}
    with PlatformDownloader(options, SOURCES[job.platform][1]) as client:
        info = client.extract_info(job.source_url, download=True)
    if not isinstance(info, dict) or "entries" in info or video_filter(info):
        raise DownloadError("Use a finished individual video or replay.")
    for path in directory.glob("video.*"):
        if path.suffix in VIDEO_EXTENSIONS and path.stem == "video" and not path.is_symlink() and path.is_file():
            if path.stat().st_size > 0:
                return info, path
    raise DownloadError("No downloadable video file was returned.")


def failure_message(exc):
    if isinstance(exc, DownloadLimit):
        return "This video exceeds the 4 GiB limit. Try a lower resolution."
    if isinstance(exc, InvalidSourceVideo):
        return "The platform returned a file without a playable video stream."
    message = str(exc).lower()
    if "live" in message or "finished" in message:
        return "Live streams are available here once the replay has finished."
    if any(term in message for term in ("403", "429", "sign in", "log in", "login", "logged-in", "private")):
        return "The source restricted this request. Check that the video is public and available in your region."
    if any(term in message for term in ("not found", "unavailable", "deleted")):
        return "The source video is unavailable. Open the link to check that it still plays."
    if "max-filesize" in message:
        return "This video exceeds the 4 GiB limit. Try a lower resolution."
    if "unsupported url" in message or "individual video" in message:
        return "Open the original video on a supported platform and copy its share link."
    if "disk" in message or "space left" in message:
        return "The download could not be saved. Check free disk space."
    return "A video file could not be downloaded. Check the link and try again."


def finish_cancelled(job_id):
    queue = Download.objects.filter(pk=job_id, status=Status.CANCELLING)
    try:
        remove_files(job_id)
    except OSError:
        LOG.warning("Could not remove incomplete video files.")
    update(queue, status=Status.CANCELLED, bytes_received=0, bytes_expected=0, activity="Download stopped.")


def process_next():
    job = Download.objects.filter(status=Status.QUEUED).order_by("created_at", "pk").first()
    if job is None:
        return False
    queue = Download.objects.filter(pk=job.pk)
    if not update(queue.filter(status=Status.QUEUED), status=Status.RUNNING, activity="Connecting to the source"):
        return True
    progress = Progress(job.pk)
    directory = download_directory(job.pk)
    try:
        remove_files(job.pk)
        directory.mkdir(parents=True)
        info, path = fetch_video(job, directory, progress)
        progress.report(activity="Checking the video file")
        size = path.stat().st_size
        if size > FILE_LIMIT:
            raise DownloadLimit
        duration, _, _ = validate_local_video(path, require_audio=False)
        title = " ".join(str(info.get("title") or "Video").split())[:240] or "Video"
        progress.report(status=Status.COMPLETE, title=title, extension=path.suffix[1:], duration=duration,
                        width=info.get("width") or None, height=info.get("height") or None,
                        bytes_received=size, bytes_expected=size, activity="Ready to save")
    except Exception as exc:
        if queue.filter(status=Status.CANCELLING).exists():
            finish_cancelled(job.pk)
        else:
            try:
                remove_files(job.pk)
            except OSError:
                LOG.warning("Could not remove incomplete video files.")
            failed = update(queue.filter(status=Status.RUNNING), status=Status.FAILED, activity=failure_message(exc),
                            bytes_received=0, bytes_expected=0)
            if failed:
                LOG.warning("Download %s failed (%s)", job.pk, type(exc).__name__)
            elif queue.filter(status=Status.CANCELLING).exists():
                finish_cancelled(job.pk)
    return True


def run_queue(*, once=False):
    root = settings.STUDIO_DATA_DIR / "downloads"
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(root / "queue.lock", timeout=0):
        for job_id in Download.objects.filter(status=Status.CANCELLING).values_list("pk", flat=True).iterator():
            finish_cancelled(job_id)
        update(Download.objects.filter(status=Status.RUNNING), status=Status.QUEUED,
               activity="Waiting to restart after interruption", bytes_received=0, bytes_expected=0)
        while True:
            worked = process_next()
            if once:
                return
            if not worked:
                time.sleep(1)
