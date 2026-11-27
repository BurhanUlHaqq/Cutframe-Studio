"""Workspace-private download queue and file serving."""

import json

from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from studio.exports import publication_filename
from studio.views import ensure_owner

from .models import Download
from .sources import FILE_LIMIT, QUALITY, SOURCES, normalize_source
from .worker import ACTIVE, TERMINAL, VIDEO_EXTENSIONS, download_directory, remove_files, update


Status = Download.Status
BATCH_LIMIT = 8
PENDING_LIMIT = 24


def payload(job):
    return {
        "id": str(job.pk), "url": job.source_url, "platform": job.platform, "quality": job.quality,
        "status": job.status, "activity": job.activity, "title": job.title, "extension": job.extension,
        "bytes_received": job.bytes_received, "bytes_expected": job.bytes_expected, "duration": job.duration,
        "width": job.width, "height": job.height, "created_at": job.created_at,
        "file_url": reverse("downloads:file", args=[job.pk]) if job.status == Status.COMPLETE else None,
    }


def error(message, *, status=400):
    return JsonResponse({"error": message}, status=status)


def owned(request):
    owner = request.session.get("studio_owner")
    return Download.objects.filter(owner=owner) if owner else Download.objects.none()


@require_http_methods(["GET", "POST"])
@never_cache
def downloads(request):
    if request.method == "GET":
        jobs = owned(request)
        page = Paginator(jobs.order_by("-created_at", "-pk"), 12).get_page(request.GET.get("page", 1))
        return JsonResponse({
            "downloads": [payload(job) for job in page], "active": jobs.filter(status__in=ACTIVE).count(),
            "page": page.number, "pages": page.paginator.num_pages, "total": page.paginator.count,
            "platforms": list(SOURCES), "qualities": QUALITY, "batch_limit": BATCH_LIMIT, "file_limit": FILE_LIMIT,
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
    if not isinstance(quality, str) or quality not in QUALITY:
        return error("Choose a resolution preference.")
    if not isinstance(urls, list) or not 1 <= len(urls) <= BATCH_LIMIT:
        return error(f"Enter 1 to {BATCH_LIMIT} video links.")
    sources = {}
    for index, url in enumerate(urls, 1):
        try:
            normalized, platform = normalize_source(url)
        except ValueError as exc:
            return error(f"Link {index}. {exc}")
        sources[normalized] = platform

    owner = ensure_owner(request)
    if owned(request).filter(status__in=ACTIVE).count() + len(sources) > PENDING_LIMIT:
        return error(f"You can have up to {PENDING_LIMIT} pending downloads. Let some finish first.", status=409)
    jobs = Download.objects.bulk_create([
        Download(owner=owner, source_url=url, platform=platform, quality=quality) for url, platform in sources.items()
    ])
    return JsonResponse({"downloads": [payload(job) for job in jobs]}, status=201)


@require_POST
@never_cache
def action(request, job_id, command):
    with transaction.atomic():
        jobs = owned(request).filter(pk=job_id)
        job = get_object_or_404(jobs.select_for_update())
        if command == "stop" and job.status == Status.QUEUED:
            update(jobs, status=Status.CANCELLED, activity="Removed from the queue.")
        elif command == "stop" and job.status == Status.RUNNING:
            update(jobs, status=Status.CANCELLING, activity="Stopping the current download")
        elif command == "retry" and job.status in {Status.FAILED, Status.CANCELLED}:
            update(jobs, status=Status.QUEUED, activity="Waiting for the downloader.", bytes_expected=0, bytes_received=0)
        elif command == "delete" and job.status in TERMINAL:
            try:
                remove_files(job.pk)
            except OSError:
                return error("Could not remove the video file. Close it and try again.", status=409)
            job.delete()
            return JsonResponse({"deleted": True})
        else:
            return error("This action is unavailable for the current download status.", status=409)
        job.refresh_from_db()
    return JsonResponse(payload(job))


@require_safe
@never_cache
def file(request, job_id):
    job = get_object_or_404(owned(request), pk=job_id, status=Status.COMPLETE)
    suffix = f".{job.extension}"
    if suffix not in VIDEO_EXTENSIONS:
        raise Http404("The downloaded file is unavailable.")
    path = download_directory(job.pk) / f"video{suffix}"
    try:
        if path.is_symlink():
            raise OSError
        stream = path.open("rb")
    except OSError as exc:
        raise Http404("The downloaded file is unavailable.") from exc
    return FileResponse(stream, as_attachment=True, filename=publication_filename(job.title) + suffix)
