"""Accept platform videos and playlist links and normalize their share URLs."""

import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from shorts_generator.url_validation import YOUTUBE_HOSTS


SOURCES = {
    "YouTube": (YOUTUBE_HOSTS, ("youtube",)),
    "Vimeo": ({"vimeo.com", "www.vimeo.com", "player.vimeo.com"}, ("vimeo",)),
    "Dailymotion": ({"dailymotion.com", "www.dailymotion.com", "dai.ly"}, ("dailymotion",)),
    "TikTok": ({"tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}, ("tiktok", "vm.tiktok")),
    "Instagram": ({"instagram.com", "www.instagram.com"}, ("instagram",)),
    "Twitch": ({"twitch.tv", "www.twitch.tv", "clips.twitch.tv"}, ("twitch:clips", "twitch:vod")),
    "Reddit": ({"reddit.com", "www.reddit.com", "old.reddit.com"}, ("reddit",)),
}
PLAYLISTS = {"YouTube": ("youtube:tab",), "Vimeo": ("vimeo:album",), "Dailymotion": ("dailymotion:playlist",)}
QUALITY = {
    "best": "Best available", "4320": "8K (4320p)", "2160": "4K (2160p)", "1440": "1440p",
    "1080": "1080p", "720": "720p", "480": "480p", "360": "360p", "240": "240p", "144": "144p",
}
BITRATES = (128, 192, 256, 320)
FILE_LIMIT = 16 * 1024**3
PLAYLIST_LIMIT = 100


def normalize_target(value, *, linked_playlist=False):
    message = "Use an HTTPS link to a video or a supported playlist."
    if not isinstance(value, str) or len(value.strip()) > 600:
        raise ValueError(message)
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(message) from exc
    if (parsed.scheme != "https" or parsed.username is not None or port not in (None, 443)
            or re.search(r"[\s\\]", value.strip())):
        raise ValueError(message)
    platform = next((name for name, (hosts, _) in SOURCES.items() if host in hosts), None)
    if platform is None:
        raise ValueError(message)

    path = parsed.path.rstrip("/")
    params = parse_qs(parsed.query)
    query = ""
    if platform == "YouTube":
        playlist = params.get("list", [""])[0]
        wants_playlist = path == "/playlist" or linked_playlist and bool(playlist)
        if wants_playlist and re.fullmatch(r"[A-Za-z0-9_-]{10,128}", playlist):
            return "https://www.youtube.com/playlist?" + urlencode({"list": playlist}), platform, True
        if host in {"youtu.be", "www.youtu.be"}:
            video = path.removeprefix("/")
        elif path == "/watch":
            video = params.get("v", [""])[0]
        else:
            video = re.sub(r"^/(?:shorts|embed|live)/", "", path)
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
            return f"https://www.youtube.com/watch?v={video}", platform, False
    elif platform == "Vimeo":
        if host != "player.vimeo.com" and re.fullmatch(r"/(?:showcase|album)/\d+", path):
            return f"https://vimeo.com{path}", platform, True
        if re.fullmatch(r"/(?:video/)?\d+(?:/[A-Za-z0-9]+)?", path):
            key = params.get("h", [""])[0]
            if host == "player.vimeo.com" and re.fullmatch(r"[A-Za-z0-9]+", key):
                query = urlencode({"h": key})
            return urlunsplit(("https", host, path, query, "")), platform, False
    else:
        if platform == "Dailymotion" and host != "dai.ly" and re.fullmatch(r"/playlist/[A-Za-z0-9]+", path):
            return f"https://www.dailymotion.com{path}", platform, True
        patterns = {
            "Dailymotion": r"/[A-Za-z0-9]+" if host == "dai.ly" else r"/(?:embed/)?video/[A-Za-z0-9]+",
            "TikTok": r"/[A-Za-z0-9]+" if host in {"vm.tiktok.com", "vt.tiktok.com"} else
                      r"/(?:t/[A-Za-z0-9]+|@[\w.-]+/video/\d+)",
            "Instagram": r"/(?:reel|reels|p|tv)/[A-Za-z0-9_-]+",
            "Twitch": r"/[A-Za-z0-9_-]+" if host == "clips.twitch.tv" else
                      r"/(?:videos/\d+|[A-Za-z0-9_]+/clip/[A-Za-z0-9_-]+)",
            "Reddit": r"/(?:r/[A-Za-z0-9_]+/)?comments/[A-Za-z0-9]+(?:/[A-Za-z0-9_-]+)?",
        }
        if re.fullmatch(patterns[platform], path):
            if host == "tiktok.com":
                host = "www.tiktok.com"
            return urlunsplit(("https", host, path, "", "")), platform, False
    raise ValueError(f"Open the {platform} video or a supported playlist and copy its share URL.")


def normalize_source(value):
    url, platform, playlist = normalize_target(value)
    if playlist:
        raise ValueError(f"Use an individual {platform} video link.")
    return url, platform
