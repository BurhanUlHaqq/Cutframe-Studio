"""Provider-specific transfers. Checkpoints precede requests that can publish."""

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.utils import timezone

from .config import PublishError, publishing_settings, seal
from .http import bearer, read_response, request, upload_url
from .models import Publication
from .oauth import graph_base
from .thumbnails import thumbnail_path, youtube_video_id


Status = Publication.Status


@dataclass
class Result:
    status: str
    message: str
    url: str = ""
    delay: int = 0
    thumbnail_state: str = ""


def checkpoint(post, data, progress=None, *, url=None):
    post.transfer = seal(data)
    fields = ["transfer", "updated_at"]
    if progress is not None:
        post.progress = progress
        fields.append("progress")
    if url:
        post.url = url
        fields.append("url")
    post.save(update_fields=fields)


def pending(post, message, delay=30):
    if (timezone.now() - post.started_at).total_seconds() >= 10 * 60:
        return Result(Status.REVIEW, "The platform is still processing. Check its status before trying another upload.")
    return Result(Status.PROCESSING, message, delay=delay)


def checked_id(value):
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_][\w~.-]{0,179}", value, flags=re.ASCII):
        raise PublishError("The platform returned an unexpected reference.")
    return value


def media_chunks(file, length):
    remaining = length
    while remaining:
        block = file.read(min(remaining, 1024 * 1024))
        if not block:
            raise PublishError("The rendered media became unavailable during transfer.")
        remaining -= len(block)
        yield block


def put_range(client, url, file, offset, end, size, headers):
    file.seek(offset)
    length = end - offset
    return request(client, "PUT", url, headers={
        **headers, "Content-Type": "video/mp4", "Content-Length": str(length),
        "Content-Range": f"bytes {offset}-{end - 1}/{size}",
    }, content=media_chunks(file, length))


def youtube_result(response, post):
    video = read_response(response)
    if not video.get("id"):
        raise PublishError("YouTube did not confirm a video ID. Check YouTube Studio.", review=True)
    video_id = checked_id(video["id"])
    url = f"https://www.youtube.com/watch?v={video_id}"
    if not youtube_video_id(url):
        raise PublishError("YouTube returned an unexpected video ID. Check YouTube Studio.", review=True)
    privacy = video.get("status", {}).get("privacyStatus", post.options["privacy"])
    if privacy not in {"private", "unlisted", "public"}:
        privacy = post.options["privacy"]
    if post.options.get("thumbnail_file"):
        # Persist the video ID before polling or sending an image. A retry only touches this video.
        state = {"video_id": video_id, "cover_since": timezone.now().timestamp()}
        checkpoint(post, state, 99, url=url)
        return Result(Status.PROCESSING, f"Uploaded as {privacy}. Waiting for YouTube to process the video before setting its thumbnail.",
                      url, delay=15, thumbnail_state="processing")
    return Result(Status.COMPLETE, f"Uploaded to YouTube with {privacy} visibility. The JPG was unavailable.",
                  url, thumbnail_state="missing")


def youtube_thumbnail(client, post, token, state):
    video_id = state["video_id"]
    if video_id != youtube_video_id(post.url):
        raise PublishError("The recorded YouTube video ID did not match the upload.", review=True)
    image = thumbnail_path(post.job, post.options.get("thumbnail_file"))
    if not image or image.stat().st_size > 50_000_000:
        raise PublishError("The saved JPG is missing or exceeds YouTube's thumbnail limit.")

    headers = bearer(token)
    response = request(client, "GET", "https://www.googleapis.com/youtube/v3/videos",
                       params={"id": video_id, "part": "processingDetails,status",
                               "fields": "items(processingDetails(processingStatus),status(uploadStatus))"},
                       headers=headers)
    videos = read_response(response).get("items") or []
    video = videos[0] if videos else {}
    processing = video.get("processingDetails", {}).get("processingStatus")
    upload = video.get("status", {}).get("uploadStatus")
    if processing in {"failed", "terminated"} or upload in {"failed", "rejected", "deleted"}:
        return Result(Status.REVIEW, "YouTube reported a problem processing this video. Check it in YouTube Studio.",
                      post.url, thumbnail_state="failed")
    if processing != "succeeded" and not (processing is None and upload == "processed"):
        if timezone.now().timestamp() - state["cover_since"] < 3600:
            return Result(Status.PROCESSING, "Waiting for YouTube to finish processing the video before setting its thumbnail.",
                          post.url, delay=30, thumbnail_state="processing")
        raise PublishError("YouTube has not finished processing the video. Retry the thumbnail when it is ready.")

    with image.open("rb") as handle:
        size = image.stat().st_size
        response = request(client, "POST", "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
                           params={"videoId": video_id, "uploadType": "media"},
                           headers={**headers, "Content-Type": "image/jpeg", "Content-Length": str(size)},
                           content=media_chunks(handle, size))
    if response.status_code == 403:
        raise PublishError("YouTube denied the thumbnail. Check the connected channel's custom thumbnail access.")
    if response.status_code == 400:
        raise PublishError("YouTube rejected the saved JPG. Check the cover image and try again.")
    read_response(response)
    return Result(Status.COMPLETE, "YouTube accepted the custom thumbnail.", post.url, thumbnail_state="applied")


def youtube_offset(response, size):
    if response.status_code == 404 or response.status_code == 410:
        raise PublishError("The YouTube upload session expired. Check YouTube Studio before creating another post.", review=True)
    if response.status_code != 308:
        read_response(response)
        raise PublishError("YouTube returned an unexpected upload status.", review=True)
    header = response.headers.get("Range", "")
    if not header:
        return 0
    match = re.fullmatch(r"bytes=0-(\d+)", header)
    if not match or int(match[1]) >= size:
        raise PublishError("YouTube returned an unexpected upload offset.", review=True)
    return int(match[1]) + 1


def youtube(client, post, token, file, size, state):
    headers = bearer(token)
    if not state:
        response = request(client, "POST", "https://www.googleapis.com/upload/youtube/v3/videos",
                           params={"uploadType": "resumable", "part": "snippet,status", "notifySubscribers": "false"},
                           headers={**headers, "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"},
                           json={"snippet": {"title": post.options["title"], "description": post.options["caption"],
                                             "categoryId": "22"},
                                 "status": {"privacyStatus": post.options["privacy"],
                                            "selfDeclaredMadeForKids": post.options["made_for_kids"],
                                            "containsSyntheticMedia": post.options.get("synthetic", False)}})
        if not response.is_success:
            read_response(response)
        url = upload_url(response.headers.get("Location"), {"www.googleapis.com", "youtube.googleapis.com"})
        checkpoint(post, {"url": url}, 1)
        offset = 0
    else:
        url = upload_url(state["url"], {"www.googleapis.com", "youtube.googleapis.com"})
        response = request(client, "PUT", url, headers={
            **headers, "Content-Range": f"bytes */{size}", "Content-Length": "0",
        })
        if response.status_code in {200, 201}:
            return youtube_result(response, post)
        offset = youtube_offset(response, size)
    while offset < size:
        end = min(size, offset + 8 * 1024 * 1024)
        response = put_range(client, url, file, offset, end, size, headers)
        if response.status_code in {200, 201}:
            return youtube_result(response, post)
        accepted = youtube_offset(response, size)
        if accepted <= offset:
            raise PublishError("YouTube has not accepted the latest video chunk yet.", retry=True)
        offset = accepted
        post.progress = min(99, offset * 100 // size)
        post.save(update_fields=["progress", "updated_at"])
    return pending(post, "YouTube is confirming the upload.")


def tiktok_status(client, token, reference):
    response = request(client, "POST", "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
                       headers=bearer(token), json={"publish_id": reference})
    return read_response(response, tiktok=True)


def tiktok_result(data):
    match data.get("status"):
        case "SEND_TO_USER_INBOX":
            return Result(Status.INBOX, "Open the draft in your TikTok inbox to choose a cover and finish posting.",
                          thumbnail_state="manual")
        case "PUBLISH_COMPLETE":
            return Result(Status.COMPLETE, "TikTok confirmed the post. Open your TikTok profile to view it.")
        case "FAILED":
            raise PublishError("TikTok could not process the video. Check media format and your account's upload limits.")
        case _:
            return None


def tiktok(client, post, token, file, size, state):
    chunk = size if size <= 64_000_000 else 32_000_000
    total = size // chunk
    hosts = {"open-upload.tiktokapis.com"}
    if not state:
        data = read_response(request(client, "POST", "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
                                     headers=bearer(token), json={"source_info": {
                                         "source": "FILE_UPLOAD", "video_size": size,
                                         "chunk_size": chunk, "total_chunk_count": total,
                                     }}), tiktok=True)
        state = {"url": upload_url(data.get("upload_url"), hosts), "id": checked_id(data["publish_id"]), "bytes": 0}
        checkpoint(post, state, 1)
    else:
        data = tiktok_status(client, token, state["id"])
        result = tiktok_result(data)
        if result:
            return result
        # A status response can lag a successful PUT. Keep the higher acknowledged offset.
        state["bytes"] = max(state["bytes"], int(data.get("uploaded_bytes") or 0))

    offset = state["bytes"]
    if offset < size and offset % chunk:
        return pending(post, "TikTok is confirming an interrupted transfer.")
    while offset < size:
        end = size if offset // chunk == total - 1 else offset + chunk
        response = put_range(client, upload_url(state["url"], hosts), file, offset, end, size, {})
        if not response.is_success:
            if response.status_code in {403, 404}:
                raise PublishError("TikTok's upload link expired. Check the TikTok inbox before uploading again.", review=True)
            read_response(response)
        offset = end
        state["bytes"] = offset
        checkpoint(post, state, min(99, offset * 100 // size))
    result = tiktok_result(tiktok_status(client, token, state["id"]))
    return result or pending(post, "TikTok is processing the inbox draft.")


def instagram(client, post, token, file, size, state):
    headers = bearer(token)
    account_id = checked_id(post.account_id)
    version = publishing_settings().meta_api_version
    base = graph_base(version)
    cover_time = post.options.get("thumbnail_time_ms")
    thumbnail_state = "frame" if cover_time is not None else ""
    if not state:
        container = read_response(request(client, "POST", f"{base}/{account_id}/media", headers=headers,
                                          json={"media_type": "REELS", "upload_type": "resumable",
                                                "caption": post.options["caption"],
                                                **({"thumb_offset": str(cover_time)} if cover_time is not None else {}),
                                                **({"is_ai_generated": True} if post.options.get("synthetic") else {})}))
        reference = checked_id(container["id"])
        address = container.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{version}/{reference}"
        state = {"id": reference, "url": upload_url(address, {"rupload.facebook.com"}), "step": "created"}
        checkpoint(post, state, 1)
    status = read_response(request(client, "GET", f"{base}/{state['id']}", params={"fields": "status_code"},
                                   headers=headers)).get("status_code")
    if status == "PUBLISHED":
        return Result(Status.COMPLETE, "Instagram confirmed the Reel. Open your Instagram profile to view it.",
                      thumbnail_state=thumbnail_state)
    if state["step"] == "publishing":
        return pending(post, "Instagram is confirming the Reel.", delay=60)
    if status in {"ERROR", "EXPIRED"}:
        raise PublishError("Instagram could not process this Reel or its container expired. Check the media requirements.")
    if state["step"] == "created" and status != "FINISHED":
        file.seek(0, os.SEEK_SET)
        upload = read_response(request(client, "POST", upload_url(state["url"], {"rupload.facebook.com"}),
                                       headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size),
                                                "Content-Type": "application/octet-stream",
                                                "Content-Length": str(size)}, content=media_chunks(file, size)))
        if not upload.get("success"):
            raise PublishError("Meta did not accept the video transfer.")
        state["step"] = "uploaded"
        checkpoint(post, state, 99)
        return pending(post, "Instagram is processing the uploaded Reel.", delay=60)
    if status != "FINISHED":
        return pending(post, "Instagram is processing the uploaded Reel.", delay=60)
    state["step"] = "publishing"
    checkpoint(post, state, 99)
    published = read_response(request(client, "POST", f"{base}/{account_id}/media_publish",
                                      headers=headers, json={"creation_id": state["id"]}))
    if not published.get("id"):
        raise PublishError("Instagram did not confirm publication. Check the Reel's status.", review=True)
    media_id = checked_id(published["id"])
    link = ""
    try:
        media = read_response(request(client, "GET", f"{base}/{media_id}", params={"fields": "permalink"}, headers=headers))
        candidate = media.get("permalink", "")
        parts = urlsplit(candidate)
        if parts.scheme == "https" and parts.hostname in {"www.instagram.com", "instagram.com"}:
            link = candidate
    except (PublishError, ValueError):
        pass
    return Result(Status.COMPLETE, "Published to Instagram Reels.", link, thumbnail_state=thumbnail_state)
