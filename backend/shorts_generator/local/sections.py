"""Fetch an exact media section using validated HTTP byte ranges or timed fragments."""

import math
import os
import re
import subprocess
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from uuid import uuid4

from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import RequestError
from yt_dlp.utils import DownloadError

from .media import InvalidSourceVideo, resolve_source_range
from .network import PlatformDownloader
from .section_hls import HLS, stage_hls_tracks


BLOCK_SIZE = 512 * 1024
DIRECT = "[protocol~='^https?$'][ext~='^(mp4|m4v|m4a|mov|webm|mkv|mp3|aac|ogg|opus)$']"
SECTION_UNAVAILABLE = "This source could not serve the selected time range. Choose a source that supports partial downloads."
CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")
DEMUXERS = "mov,matroska,webm,mp3,aac,ogg,mpegts"
SECTION_ERRORS = (InvalidSourceVideo, OSError, RequestError)
SEGMENTED_PROTOCOLS = {"m3u8", "m3u8_native"}


def video_within_8k(fmt):
    dimensions = [value for value in (fmt.get("width"), fmt.get("height")) if isinstance(value, (int, float))]
    return not dimensions or max(dimensions) <= 7680 and (len(dimensions) == 1 or min(dimensions) <= 4320)


def section_selector(protocol, audio_only):
    if audio_only:
        return f"ba{protocol}/b{protocol}"
    return f"bv*{protocol}+ba{protocol}/b{protocol}/bv*{protocol}"


def section_options(quality, audio_bitrate):
    resolution = "res" if quality == "best" else f"res:{quality}"
    audio_only = audio_bitrate is not None
    options = {
        "format": f"{section_selector(DIRECT, audio_only)}/{section_selector(HLS, audio_only)}",
        "format_sort": ["abr", "asr"] if audio_only else [resolution, "ext:mp4:m4a"],
        "format_sort_force": True, "quiet": True, "no_warnings": True, "noplaylist": True,
        "playlistend": 1, "socket_timeout": 18, "proxy": "",
    }
    if which("node"):
        options["js_runtimes"] = {"node": {}}
    return options


def read_block(client, media, start, end):
    headers = {key: value for key, value in (media.get("http_headers") or {}).items()
               if key.lower() not in {"range", "accept-encoding", "host", "content-length"}}
    headers.update({"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"})
    with client.urlopen(Request(media["url"], headers=headers)) as response:
        match = CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
        if response.status != 206 or not match:
            raise InvalidSourceVideo(SECTION_UNAVAILABLE)
        first, last, size = map(int, match.groups())
        if (start != first or last != min(end, size - 1) or last < first or
                response.headers.get("Content-Encoding", "identity").lower() != "identity"):
            raise InvalidSourceVideo(SECTION_UNAVAILABLE)
        length = last - first + 1
        block = response.read(length)
        if len(block) != length:
            raise InvalidSourceVideo(SECTION_UNAVAILABLE)
        return block, size


@contextmanager
def section_relay(client, formats, fraction):
    """Allow FFmpeg to seek within selected files without giving it their remote URLs."""
    token = uuid4().hex
    media = {}
    for index, fmt in enumerate(formats):
        _, size = read_block(client, fmt, 0, 0)
        media[f"/{token}/{index}"] = (fmt, size)
    # Keep room for container indexes, keyframes and VBR peaks. Abort sources
    # that try to scan most of a long file for a short clip.
    total = sum(size for _, size in media.values())
    limit = int(total * min(1, fraction * 4)) + 8 * BLOCK_SIZE

    class RangeHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, pattern, *args):
            pass

        def do_GET(self):
            item = media.get(self.path)
            if not item:
                self.send_error(404)
                return
            fmt, size = item
            requested = re.fullmatch(r"bytes=(\d{1,20})-(\d{0,20})", self.headers.get("Range", ""))
            if not requested:
                self.send_error(416)
                return
            start = int(requested[1])
            end = min(int(requested[2]) if requested[2] else size - 1, size - 1, start + BLOCK_SIZE - 1)
            if start > end:
                self.send_error(416)
                return
            length = end - start + 1
            with self.server.transfer_lock:
                within_limit = self.server.transferred + length <= limit
                if within_limit:
                    self.server.transferred += length
            if not within_limit:
                self.server.failed = True
                self.send_error(502)
                return
            try:
                block, actual_size = read_block(client, fmt, start, end)
                if actual_size != size:
                    raise InvalidSourceVideo(SECTION_UNAVAILABLE)
            except Exception:
                self.server.failed = True
                self.send_error(502)
                return
            try:
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(length))
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                self.wfile.write(block)
            except (BrokenPipeError, ConnectionResetError):
                pass

    with ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler) as server:
        server.daemon_threads = True
        server.transferred = len(media)
        server.transfer_lock = Lock()
        server.failed = False
        running = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        running.start()
        try:
            yield [f"http://127.0.0.1:{server.server_port}{path}" for path in media], server
        finally:
            server.shutdown()
            running.join()


def extract_section(url, destination, source_range, *, extractors, quality="best", audio_bitrate=None, on_progress=None):
    """Return extractor metadata and actual source bounds for a downloaded clip."""
    options = section_options(quality, audio_bitrate)
    try:
        with PlatformDownloader(options, extractors) as client:
            if audio_bitrate is None:
                select = client.build_format_selector(options["format"])
                client.params["format"] = lambda context: select(
                    {**context, "formats": [fmt for fmt in context["formats"] if video_within_8k(fmt)]}
                )
            info = client.extract_info(url, download=False)
            if not isinstance(info, dict) or "entries" in info or info.get("is_live"):
                raise InvalidSourceVideo("Choose a finished individual video for a timestamp download.")
            duration = info.get("duration")
            if not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration <= 0:
                raise InvalidSourceVideo("This source did not report a duration for a timestamp download.")
            start, end = resolve_source_range(duration, *source_range)
            formats = info.get("requested_formats") or [info]
            if not all(fmt.get("protocol") in SEGMENTED_PROTOCOLS for fmt in formats):
                try:
                    with section_relay(client, formats, (end - start) / duration) as (inputs, relay):
                        encode_section([(item, start) for item in inputs], destination, end - start,
                                       audio_bitrate, on_progress, relay=relay)
                    return info, start, end
                except SECTION_ERRORS as exc:
                    selector = client.build_format_selector(section_selector(HLS, audio_bitrate is not None))
                    candidates = [fmt for fmt in info.get("formats", []) if video_within_8k(fmt)
                                  and fmt.get("protocol") in SEGMENTED_PROTOCOLS]
                    selected = next(iter(selector({"formats": candidates, "has_merged_format": True,
                                                   "incomplete_formats": False})), None)
                    if selected is None:
                        raise InvalidSourceVideo(SECTION_UNAVAILABLE) from exc
                    formats = selected.get("requested_formats") or [selected]

            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with TemporaryDirectory(prefix=".fragments-", dir=destination.parent) as temporary:
                    inputs, received = stage_hls_tracks(
                        client, formats, Path(temporary), start, end, duration, on_progress,
                    )
                    encode_section(inputs, destination, end - start, audio_bitrate, on_progress, received=received)
            except SECTION_ERRORS as exc:
                raise InvalidSourceVideo(SECTION_UNAVAILABLE) from exc
    except (DownloadError, OSError, RequestError) as exc:
        raise InvalidSourceVideo(SECTION_UNAVAILABLE) from exc
    return info, start, end


def encode_section(inputs, destination, length, audio_bitrate, on_progress, *, relay=None, received=0):
    command = ["ffmpeg", "-nostdin", "-loglevel", "error", "-xerror", "-progress", "pipe:1"]
    for source, offset in inputs:
        protocols = "http,tcp" if relay else "file"
        command.extend(["-ss", f"{offset:.3f}", "-t", f"{length:.3f}", "-protocol_whitelist", protocols,
                        "-format_whitelist", DEMUXERS])
        if relay:
            command.extend(["-rw_timeout", "25000000", "-seekable", "1", "-request_size", str(BLOCK_SIZE),
                            "-initial_request_size", str(BLOCK_SIZE), "-short_seek_size", str(BLOCK_SIZE)])
        command.extend(["-i", source])
    if audio_bitrate is not None:
        command.extend(["-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", f"{audio_bitrate}k"])
    else:
        command.extend(["-map", "0:V:0", "-map", f"{len(inputs) - 1}:a:0?", "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"])
    destination = Path(destination)
    environment = {key: value for key, value in os.environ.items() if key.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".section-", dir=destination.parent) as temporary:
        pending = Path(temporary) / destination.name
        with subprocess.Popen([*command, "-t", f"{length:.3f}", str(pending)], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, env=environment) as process:
            try:
                actual_time = 0
                for line in process.stdout:
                    if line.startswith("out_time_us=") and (value := line.partition("=")[2].strip()).isdigit():
                        actual_time = max(actual_time, int(value) / 1_000_000)
                        if on_progress:
                            portion = min(1, actual_time / length)
                            if relay:
                                on_progress(relay.transferred, portion)
                            else:
                                on_progress(received, 0.8 + 0.2 * portion)
                complete = process.wait() == 0 and (relay is None or not relay.failed)
            finally:
                if process.poll() is None:
                    process.kill()
        if not complete or not pending.is_file() or pending.stat().st_size == 0 or actual_time < length - min(0.2, length / 4):
            raise InvalidSourceVideo(SECTION_UNAVAILABLE)
        pending.replace(destination)
