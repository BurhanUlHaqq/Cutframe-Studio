"""Workspace library and manual cleanup of render-owned media."""

import json
import logging
import os
import shutil
from datetime import timedelta
from stat import S_ISREG
from uuid import UUID

from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from filelock import Timeout

from publishing.models import Publication

from .exports import job_directory, render_lock
from .models import Job, StudioMode
from .source_reuse import sources_in_use


logger = logging.getLogger(__name__)
PAGE_SIZE = 12
REMOVABLE = (Job.Status.COMPLETE, Job.Status.FAILED, Job.Status.CANCELLED, Job.Status.DELETING)
PUBLISHING_REASON = "This render has a scheduled or active publication. Cancel it or wait for it to finish."
SOURCE_IN_USE = "Another render still needs this upload. Let it prepare its source or remove the alternate render first."


def render_bytes(directory):
    if directory.is_symlink():
        return 0
    total = 0
    for folder, _, names in os.walk(directory, followlinks=False):
        for name in names:
            try:
                entry = os.stat(os.path.join(folder, name), follow_symlinks=False)
                if S_ISREG(entry.st_mode):
                    total += entry.st_size
            except OSError:
                continue
    return total


def library_status(job):
    if job.status == Job.Status.COMPLETE and not job.clips:
        return "empty"
    return job.status


def summarize(job, bytes_used, publishing, source_in_use):
    ready = job.status == Job.Status.COMPLETE and bool(job.clips)
    first_clip = job.clips[0] if ready else {}
    thumbnail = reverse("media", args=[job.id, 0, "thumbnail"]) if ready else None
    if ready and first_clip.get("thumbnail_file"):
        thumbnail += f"?v={first_clip['thumbnail_file']}"
    blocked = ("Wait for this render to finish before deleting it." if job.status not in REMOVABLE else
               PUBLISHING_REASON if publishing else SOURCE_IN_USE if source_in_use else "")
    return {
        "id": str(job.id), "title": first_clip.get("title") or job.source_name or f"YouTube video {job.video_id}",
        "source": job.source_name or f"YouTube · {job.video_id}", "studio_mode": job.studio_mode,
        "status": library_status(job), "created_at": job.created_at, "progress": job.progress,
        "clip_count": len(job.clips), "bytes": bytes_used, "editing_style": job.editing_style,
        "work_bytes": render_bytes(job_directory(job.id) / "work") if job.status == Job.Status.COMPLETE else 0,
        "aspect_ratio": job.aspect_ratio, "thumbnail_url": thumbnail,
        "archive_url": reverse("archive", args=[job.id]) if ready else None, "delete_blocked": blocked,
        "source_in_use": source_in_use,
    }


@require_GET
@never_cache
def library(request):
    owner = request.session.get("studio_owner")
    filters = request.GET
    rows = Job.objects.filter(owner=owner).order_by("-created_at", "id") if owner else Job.objects.none()
    mode = filters.get("mode")
    if mode in StudioMode.values:
        rows = rows.filter(studio_mode=mode)
    age = filters.get("age")
    if age in {"30", "90", "365"}:
        rows = rows.filter(created_at__lt=timezone.now() - timedelta(days=int(age)))
    status = filters.get("status")
    if status == "active":
        rows = rows.filter(status__in=Job.ACTIVE)
    elif status in {"complete", "empty"}:
        rows = rows.filter(status=Job.Status.COMPLETE)
        rows = rows.exclude(clips=[]) if status == "complete" else rows.filter(clips=[])
    elif status in (Job.Status.FAILED, Job.Status.CANCELLED, Job.Status.DELETING):
        rows = rows.filter(status=status)

    term = filters.get("q", "").strip()[:180].casefold()
    order = filters.get("sort")
    if order == "oldest":
        rows = rows.order_by("created_at", "id")
    rows = rows.only("source_url", "video_id", "source_name", "studio_mode", "status", "created_at", "progress",
                     "clips", "editing_style", "aspect_ratio")
    if term:
        rows = [job for job in rows.iterator(chunk_size=100) if any(term in value.casefold() for value in (
            job.source_name, job.source_url, job.video_id, *(clip.get("title", "") for clip in job.clips),
        ))]

    sizes = {}
    if order == "largest":
        rows = list(rows)
        sizes = {job.id: render_bytes(job_directory(job.id)) for job in rows}
        rows.sort(key=lambda job: sizes[job.id], reverse=True)

    total = len(rows) if isinstance(rows, list) else rows.count()
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = filters.get("page", "1")
    page = min(pages, max(1, int(page))) if 0 < len(page) < 10 and page.isascii() and page.isdecimal() else 1
    current = list(rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE])
    job_ids = [job.id for job in current]
    pending = set(Publication.objects.filter(job_id__in=job_ids, status__in=Publication.ACTIVE)
                  .values_list("job_id", flat=True))
    reused = sources_in_use(job_ids)
    return JsonResponse({"renders": [summarize(job, sizes[job.id] if job.id in sizes else render_bytes(job_directory(job.id)),
                                              job.id in pending, job.id in reused) for job in current],
                         "total": total, "page": page, "pages": pages})


def remove_render(owner, job_id, *, work_only=False):
    record = Job.objects.filter(pk=job_id, owner=owner)
    if not record.exists():
        return None, "This render is no longer available."
    try:
        with render_lock(job_id):
            status = record.values_list("status", flat=True).first()
            if status is None:
                return None, "This render is no longer available."
            if work_only and status != Job.Status.COMPLETE:
                return None, "Only completed renders can have their working files cleared."
            if not work_only and status not in REMOVABLE:
                return None, "Wait for this render to finish before deleting it."
            if not work_only and Publication.objects.filter(job_id=job_id, status__in=Publication.ACTIVE).exists():
                return None, PUBLISHING_REASON
            if not work_only and sources_in_use([job_id]):
                return None, SOURCE_IN_USE
            directory = job_directory(job_id)
            target = directory / "work" if work_only else directory
            # Only remove the UUID directory inside Cutframe's jobs directory.
            if directory.is_symlink() or directory.parent.is_symlink() or target.is_symlink():
                return None, "Cutframe could not verify the render's storage location."
            if not work_only:
                record.update(status=Job.Status.DELETING, updated_at=timezone.now())
            size = render_bytes(target)
            try:
                if target.exists():
                    shutil.rmtree(target)
            except OSError as exc:
                logger.warning("Render cleanup failed for %s (%s)", job_id, type(exc).__name__)
                return None, "Some files could not be removed. Close any open downloads and try again."
            if not work_only:
                record.delete()
    except Timeout:
        return None, "This render is in use. Wait for its current operation to finish and try again."
    return size, ""


@require_POST
@never_cache
def delete_renders(request, *, work_only=False):
    if request.content_type != "application/json":
        return JsonResponse({"error": "Send the selection as JSON."}, status=415)
    try:
        payload = json.loads(request.body)
        ids = payload.get("ids") if isinstance(payload, dict) else None
        if not isinstance(ids, list) or not 1 <= len(ids) <= PAGE_SIZE:
            raise ValueError
        if not all(isinstance(item, str) for item in ids):
            raise ValueError
        ids = list(dict.fromkeys(UUID(item) for item in ids))
    except (ValueError, TypeError):
        return JsonResponse({"error": f"Select between 1 and {PAGE_SIZE} valid renders."}, status=400)
    removed, blocked = [], []
    owner = request.session.get("studio_owner")
    for job_id in ids:
        size, reason = remove_render(owner, job_id, work_only=work_only)
        if reason:
            blocked.append({"id": str(job_id), "reason": reason})
        else:
            removed.append({"id": str(job_id), "bytes_freed": size})
    return JsonResponse({"removed": removed, "blocked": blocked})
