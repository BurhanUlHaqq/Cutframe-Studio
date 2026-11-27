"""Read the media fragments overlapping a finished HLS video's time range."""

import math
import re
import time
from urllib.parse import urljoin

from yt_dlp.networking import Request

from .media import InvalidSourceVideo


PLAYLIST_LIMIT = 4 * 1024 * 1024
FRAGMENT_LIMIT = 128 * 1024 * 1024
HLS = "[protocol~='^m3u8(_native)?$']"


def media_request(fmt, url):
    headers = {key: value for key, value in (fmt.get("http_headers") or {}).items()
               if key.lower() not in {"range", "accept-encoding", "host", "content-length"}}
    return Request(url, headers={**headers, "Accept-Encoding": "identity"})


def media_fragments(client, fmt, start, end, duration):
    invalid = "This stream does not provide a finished, seekable media playlist."
    with client.urlopen(media_request(fmt, fmt["url"])) as response:
        data = response.read(PLAYLIST_LIMIT + 1)
        base_url = response.url
    if len(data) > PLAYLIST_LIMIT:
        raise InvalidSourceVideo(invalid)
    lines = [line.strip() for line in data.decode("utf-8", errors="replace").splitlines()]
    if not lines or lines[0] != "#EXTM3U" or "#EXT-X-ENDLIST" not in lines:
        raise InvalidSourceVideo(invalid)

    position = 0.0
    first_start = None
    segment_length = None
    initialization = None
    fragments = []
    for line in lines[1:]:
        if line.startswith(("#EXT-X-BYTERANGE", "#EXT-X-DISCONTINUITY", "#EXT-X-GAP", "#EXT-X-SKIP")):
            raise InvalidSourceVideo(invalid)
        if line.startswith("#EXT-X-KEY:") and line != "#EXT-X-KEY:METHOD=NONE":
            raise InvalidSourceVideo(invalid)
        if line.startswith("#EXT-X-MAP:"):
            attributes = line.partition(":")[2]
            uri = re.search(r'(?:^|,)URI="([^"]+)"', attributes)
            if not uri or "BYTERANGE=" in attributes or position:
                raise InvalidSourceVideo(invalid)
            initialization = urljoin(base_url, uri[1])
        elif line.startswith("#EXTINF:"):
            try:
                segment_length = float(line.partition(":")[2].partition(",")[0])
            except ValueError as exc:
                raise InvalidSourceVideo(invalid) from exc
            if not math.isfinite(segment_length) or segment_length <= 0:
                raise InvalidSourceVideo(invalid)
        elif line and not line.startswith("#"):
            if segment_length is None:
                raise InvalidSourceVideo(invalid)
            segment_end = position + segment_length
            if segment_end > start and position < end:
                if first_start is None:
                    first_start = position
                fragments.append(urljoin(base_url, line))
            position = segment_end
            segment_length = None

    # Source duration can be rounded by the extractor. Reject a shifted or
    # incomplete timeline before requesting the media fragments.
    if not fragments or abs(position - duration) > 1:
        raise InvalidSourceVideo(invalid)
    return initialization, fragments, start - first_start


def stage_hls_tracks(client, formats, folder, start, end, duration, on_progress):
    tracks = [(fmt, *media_fragments(client, fmt, start, end, duration)) for fmt in formats]
    total = sum(len(fragments) + bool(initialization) for _, initialization, fragments, _ in tracks)
    received = completed = 0
    last_update = 0.0
    inputs = []
    for index, (fmt, initialization, fragments, offset) in enumerate(tracks):
        path = folder / f"track-{index}{'.mp4' if initialization else '.ts'}"
        with path.open("wb") as track:
            for url in ([initialization] if initialization else []) + fragments:
                size = 0
                with client.urlopen(media_request(fmt, url)) as response:
                    if response.status != 200:
                        raise InvalidSourceVideo("The media server returned an incomplete fragment.")
                    while block := response.read(128 * 1024):
                        size += len(block)
                        received += len(block)
                        if size > FRAGMENT_LIMIT:
                            raise InvalidSourceVideo("A media fragment exceeded the download size limit.")
                        now = time.monotonic()
                        if on_progress and now - last_update >= 0.5:
                            on_progress(received, 0.8 * completed / total)
                            last_update = now
                        track.write(block)
                if size == 0:
                    raise InvalidSourceVideo("The media server returned an empty fragment.")
                completed += 1
        inputs.append((str(path), offset))
    if on_progress:
        on_progress(received, 0.8)
    return inputs, received
