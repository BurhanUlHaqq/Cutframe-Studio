"""Workspace-private download queue and file serving."""

import json
import mimetypes

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST, require_safe

from shorts_generator.timestamps import parse_source_timestamp
from studio.exports import publication_filename
from studio.views import ensure_owner, range_response

from .archive import archive_chunks
from .models import Download
from .sources import BITRATES, FILE_LIMIT, PLAYLIST_LIMIT, PLAYLISTS, QUALITY, SOURCES, normalize_target
from .worker import ACTIVE, TERMINAL, asset_path, remove_files, update


Status = Download.Status
BATCH_LIMIT = 8
PENDING_LIMIT = 24


def payload(job):
    body = {
        "id": str(job.pk), "url": job.source_url, "platform": job.platform, "quality": job.quality,
        "status": job.status, "activity": job.activity, "title": job.title, "extension": job.extension,
        "bytes_received": job.bytes_received, "bytes_expected": job.bytes_expected, "duration": job.duration,
        "width": job.width, "height": job.height, "created_at": job.created_at,
        "output": job.output, "audio_bitrate": job.audio_bitrate, "position": job.position,
        "is_playlist": job.is_playlist, "source_start_ms": job.source_start_ms, "source_end_ms": job.source_end_ms,
        "file_url": reverse("downloads:file", args=[job.pk]) if job.status == Status.COMPLETE and not job.is_playlist else None,
    }
    if job.is_playlist:
        counts = job.items.aggregate(total=Count("pk"), ready=Count("pk", filter=Q(status=Status.COMPLETE)),
                                     failed=Count("pk", filter=Q(status=Status.FAILED)))
        body["playlist"] = {**counts, "start": job.playlist_start, "end": job.playlist_end,
                            "limited": job.playlist_limited,
                            "items_url": reverse("downloads:items", args=[job.pk]),
                            "zip_url": reverse("downloads:archive", args=[job.pk])
                            if counts["ready"] and job.status in TERMINAL else None}
    return body


def error(message, *, status=400):
    return JsonResponse({"error": message}, status=status)


def owned(request):
    owner = request.session.get("studio_owner")
    return Download.objects.filter(owner=owner) if owner else Download.objects.none()


@require_http_methods(["GET", "POST"])
@never_cache
def downloads(request):
    if request.method == "GET":
        jobs = owned(request).filter(parent__isnull=True)
        page = Paginator(jobs.order_by("-created_at", "-pk"), 12).get_page(request.GET.get("page", 1))
        return JsonResponse({
            "downloads": [payload(job) for job in page], "active": jobs.filter(status__in=ACTIVE).count(),
            "page": page.number, "pages": page.paginator.num_pages, "total": page.paginator.count,
            "platforms": list(SOURCES), "qualities": QUALITY, "audio_bitrates": BITRATES,
            "batch_limit": BATCH_LIMIT, "file_limit": FILE_LIMIT,
            "playlist_platforms": list(PLAYLISTS), "playlist_limit": PLAYLIST_LIMIT,
        })
    if request.content_type != "application/json":
        return error("Send the download links as JSON.", status=415)
    try:
        data = json.loads(request.body)
    except (ValueError, RecursionError):
        return error("The download request could not be read.")
    if not isinstance(data, dict):
        return error("Send the download links as a list.")
    urls = data.get("urls")
    quality = data.get("quality", "best")
    output = data.get("output", "video")
    bitrate = data.get("audio_bitrate", 192)
    start, end = data.get("playlist_start", 1), data.get("playlist_end")
    linked = data.get("linked_playlist", False)
    if output not in ("video", "mp3"):
        return error("Choose video or MP3 output.")
    if not isinstance(quality, str) or quality not in QUALITY:
        return error("Choose a resolution preference.")
    if type(bitrate) is not int or bitrate not in BITRATES:
        return error("Choose a supported MP3 bitrate.")
    if (type(start) is not int or not 1 <= start <= 1_000_000 or
            end is not None and (type(end) is not int or not start <= end < start + PLAYLIST_LIMIT)):
        return error(f"Enter a valid playlist range of up to {PLAYLIST_LIMIT} items.")
    if type(linked) is not bool:
        return error("Choose how to handle links that include a playlist.")
    if not isinstance(urls, list) or not 1 <= len(urls) <= BATCH_LIMIT:
        return error(f"Enter 1 to {BATCH_LIMIT} video links.")
    try:
        source_start = parse_source_timestamp(data.get("source_start"))
        source_end = parse_source_timestamp(data.get("source_end"))
    except ValueError as exc:
        return error(str(exc))
    if (source_start is None) != (source_end is None):
        return error("Enter both a start time and an end time.")
    if source_start is not None and source_end <= source_start:
        return error("The end time must be after the start time.")
    sources = {}
    for index, url in enumerate(urls, 1):
        try:
            normalized, platform, playlist = normalize_target(url, linked_playlist=linked)
        except ValueError as exc:
            return error(f"Link {index}. {exc}")
        if playlist and source_start is not None:
            return error(f"Link {index}. Timestamp cuts apply to individual videos. Use Playlist options to choose entries.")
        sources[normalized] = (platform, playlist)

    owner = ensure_owner(request)
    if owned(request).filter(parent__isnull=True, status__in=ACTIVE).count() + len(sources) > PENDING_LIMIT:
        return error(f"You can have up to {PENDING_LIMIT} pending downloads. Let some finish first.", status=409)
    jobs = Download.objects.bulk_create([
        Download(owner=owner, source_url=url, platform=platform, quality=quality, output=output, audio_bitrate=bitrate,
                 is_playlist=playlist, playlist_start=start if playlist else 1, playlist_end=end if playlist else None,
                 source_start_ms=source_start, source_end_ms=source_end)
        for url, (platform, playlist) in sources.items()
    ])
    return JsonResponse({"downloads": [payload(job) for job in jobs]}, status=201)


@require_POST
@never_cache
def action(request, job_id, command):
    with transaction.atomic():
        jobs = owned(request).filter(pk=job_id, parent__isnull=True)
        job = get_object_or_404(jobs.select_for_update())
        if command == "stop" and job.status == Status.QUEUED:
            update(jobs, status=Status.CANCELLED, activity="Removed from the queue.")
        elif command == "stop" and job.status == Status.RUNNING:
            update(jobs, status=Status.CANCELLING, activity="Stopping the current download")
        elif command == "retry" and job.status in {Status.FAILED, Status.CANCELLED, Status.PARTIAL}:
            if job.is_playlist:
                unfinished = job.items.exclude(status=Status.COMPLETE).exclude(source_url="")
                update(unfinished, status=Status.QUEUED, activity="Waiting for playlist item.",
                       bytes_expected=0, bytes_received=0)
            update(jobs, status=Status.QUEUED, activity="Waiting for the downloader.", bytes_expected=0, bytes_received=0)
        elif command == "delete" and job.status in TERMINAL:
            try:
                remove_files(job)
            except OSError:
                return error("Could not remove the video file. Close it and try again.", status=409)
            job.delete()
            return JsonResponse({"deleted": True})
        else:
            return error("This action is unavailable for the current download status.", status=409)
        job.refresh_from_db()
    return JsonResponse(payload(job))


@require_GET
@never_cache
def items(request, job_id):
    job = get_object_or_404(owned(request), pk=job_id, is_playlist=True, parent__isnull=True)
    return JsonResponse({"items": [payload(item) for item in job.items.order_by("position", "pk")]})


@require_safe
@never_cache
def file(request, job_id):
    job = get_object_or_404(owned(request), pk=job_id, status=Status.COMPLETE, is_playlist=False)
    try:
        path = asset_path(job)
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = range_response(request, path, content_type=kind)
    except OSError as exc:
        raise Http404("The downloaded file is unavailable.") from exc
    response["Content-Disposition"] = content_disposition_header(True, publication_filename(job.title) + path.suffix)
    return response


@require_safe
@never_cache
def archive(request, job_id):
    job = get_object_or_404(owned(request), pk=job_id, is_playlist=True, parent__isnull=True, status__in=TERMINAL)
    entries = []
    try:
        for item in job.items.filter(status=Status.COMPLETE).order_by("position", "pk"):
            path = asset_path(item)
            if not path.is_file():
                raise FileNotFoundError
            title = publication_filename(item.title)
            entries.append((f"{item.position:03d} - {title}{path.suffix}", path))
    except OSError as exc:
        raise Http404("A downloaded item is unavailable.") from exc
    if not entries:
        raise Http404
    response = (HttpResponse(content_type="application/zip") if request.method == "HEAD" else
                StreamingHttpResponse(archive_chunks(entries), content_type="application/zip"))
    response["Content-Disposition"] = content_disposition_header(True, publication_filename(job.title or "Playlist") + ".zip")
    return response
