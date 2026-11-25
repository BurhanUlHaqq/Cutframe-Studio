import json
import re
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError, connection
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods, require_safe
from filelock import FileLock

from shorts_generator.local.llm import RankingConfigurationError, RankingModelUnavailable, check_connection, list_ranking_models

from .ai_settings import (
    AISettingsForm, ModelDiscoveryForm, ai_settings_data, merge_ai_options, resolve_ai_options, saved_ai_options,
)
from .exports import clip_text, job_directory, open_archive, open_published_video, publication_filename
from .forms import ClipTextForm, JobForm, PreferencesForm, UploadJobForm
from .models import PREFERENCE_FIELDS, RENDER_OPTIONS, Job, WorkspaceAISettings, WorkspacePreferences
from .uploads import VIDEO_EXTENSIONS, store_upload


def render_data(job, *, include_activity=True):
    clips = []
    for index, clip in enumerate(job.clips):
        media = {
            f"{kind}_url": reverse("media", args=[job.id, index, kind])
            for kind in ("video", "thumbnail", "text")
        }
        media["edit_url"] = reverse("edit_clip", args=[job.id, index])
        if clip.get("has_captions"):
            media["captions_url"] = reverse("media", args=[job.id, index, "captions"])
        clips.append({key: value for key, value in clip.items() if key not in {"name", "embedded_title"}} | media)
    return {
        "id": str(job.id), "video_id": job.video_id, "source_url": job.source_url,
        "source_name": job.source_name, "source_type": "upload" if job.source_name else "youtube",
        "selection_mode": job.selection_mode, "min_viral_score": job.min_viral_score,
        "clip_count": job.clip_count, "aspect_ratio": job.aspect_ratio, "quality": job.quality,
        "framing": job.framing, "caption_style": job.caption_style,
        "min_clip_seconds": job.min_clip_seconds, "max_clip_seconds": job.max_clip_seconds,
        "status": job.status, "stage": job.stage, "progress": job.progress,
        "created_at": job.created_at.isoformat(), "error": job.error, "clips": clips,
        "activity": job.activity,
        **({"events": job.events} if include_activity else {}),
        "archive_url": reverse("archive", args=[job.id]) if job.status == Job.Status.COMPLETE and clips else None,
    }


def owned_job(request, job_id):
    owner = request.session.get("studio_owner")
    if not owner:
        raise Http404
    return get_object_or_404(Job, pk=job_id, owner=owner)


def ensure_owner(request):
    owner = request.session.get("studio_owner")
    if not owner:
        owner = request.session["studio_owner"] = str(uuid4())
    return owner


def saved_preferences(owner):
    if owner:
        saved = WorkspacePreferences.objects.filter(owner=owner).first()
        if saved is not None:
            return saved
    return WorkspacePreferences()


def preference_values(preferences, fields=PREFERENCE_FIELDS):
    return {field: getattr(preferences, field) for field in fields}


def form_data(request, form_class, defaults=None):
    if request.content_type != "application/json":
        return None, JsonResponse({"error": "Send the form as JSON."}, status=415)
    try:
        payload = json.loads(request.body)
    except ValueError:
        return None, JsonResponse({"error": "The request could not be read."}, status=400)
    if not isinstance(payload, dict):
        return None, JsonResponse({"error": "Send a form object."}, status=400)
    form = form_class((defaults or {}) | payload)
    if not form.is_valid():
        return None, JsonResponse({"fields": form.errors.get_json_data()}, status=400)
    return form.cleaned_data, None


@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return JsonResponse({"status": "database unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@require_GET
@never_cache
def session(request):
    ensure_owner(request)
    return JsonResponse({"csrf_token": get_token(request)})


@require_http_methods(["GET", "POST"])
@never_cache
def preferences(request):
    defaults = preference_values(WorkspacePreferences())
    if request.method == "GET":
        saved = saved_preferences(request.session.get("studio_owner"))
    else:
        owner = request.session.get("studio_owner")
        data, error = form_data(request, PreferencesForm, defaults=preference_values(saved_preferences(owner)))
        if error is not None:
            return error
        saved, _ = WorkspacePreferences.objects.update_or_create(owner=ensure_owner(request), defaults=data)
    return JsonResponse({"preferences": preference_values(saved), "defaults": defaults})


@require_http_methods(["GET", "POST"])
@never_cache
def ai_settings(request):
    options = saved_ai_options(request.session.get("studio_owner"))
    if request.method == "POST":
        data, error = form_data(request, AISettingsForm)
        if error is not None:
            return error
        options = merge_ai_options(data, options)
        WorkspaceAISettings.objects.update_or_create(owner=ensure_owner(request), defaults={"config": options})
    return JsonResponse(ai_settings_data(options))


@require_POST
@never_cache
def ai_connection(request, *, discover=False):
    data, error = form_data(request, ModelDiscoveryForm if discover else AISettingsForm)
    if error is not None:
        return error
    options = merge_ai_options(data, saved_ai_options(request.session.get("studio_owner")))
    try:
        config = resolve_ai_options(options)
        if discover:
            return JsonResponse({"models": list_ranking_models(config)})
        check_connection(config)
    except (RankingConfigurationError, RankingModelUnavailable) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if discover and status in (404, 405, 501):
            return JsonResponse({"error": "The endpoint did not expose a model list. Check the base URL or enter the model ID manually."},
                                status=400)
        message = {
            401: "The provider rejected the API key. Check the credential.",
            403: "The provider denied access. Check the key and model permissions.",
            404: "The model or API route was not found. Check the model ID and API base URL.",
            429: "The provider returned a rate or quota limit.",
        }.get(status, "The provider could not be reached. Check the API base URL and make sure the server is running.")
        return JsonResponse({"error": message}, status=400)
    return JsonResponse({"message": "The selected model is visible to Cutframe."})


@never_cache
def jobs(request):
    owner = request.session.get("studio_owner")
    if request.method == "GET":
        renders = Job.objects.filter(owner=owner)[:50] if owner else []
        return JsonResponse({
            "jobs": [render_data(job, include_activity=False) for job in renders],
            "upload": {"max_bytes": settings.STUDIO_MAX_UPLOAD_BYTES, "extensions": VIDEO_EXTENSIONS},
        })
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405, headers={"Allow": "GET, POST"})
    defaults = preference_values(saved_preferences(owner), RENDER_OPTIONS)
    data, error = form_data(request, JobForm, defaults=defaults)
    if error is not None:
        return error
    url = data["source_url"]
    job = Job.objects.create(owner=ensure_owner(request), video_id=url.rsplit("=", 1)[1], **data)
    return JsonResponse(render_data(job), status=201)


@require_POST
@never_cache
def upload_job(request):
    if request.content_type != "multipart/form-data":
        return JsonResponse({"error": "Send the video as a multipart upload."}, status=415)
    defaults = preference_values(saved_preferences(request.session.get("studio_owner")), RENDER_OPTIONS)
    form = UploadJobForm(defaults | request.POST.dict(), request.FILES)
    if not form.is_valid():
        return JsonResponse({"fields": form.errors.get_json_data()}, status=400)
    data = form.cleaned_data
    video = data.pop("video")
    job = Job(owner=ensure_owner(request), source_name=video.name, **data)
    try:
        source = store_upload(job.id, video)
    except OSError:
        return JsonResponse({"error": "Cutframe could not store the upload. Check available disk space and retry."}, status=507)
    try:
        job.save(force_insert=True)
    except Exception:
        source.unlink()
        source.parent.rmdir()
        raise
    return JsonResponse(render_data(job), status=201)


@require_GET
@never_cache
def detail(request, job_id):
    return JsonResponse(render_data(owned_job(request, job_id)))


@require_POST
@never_cache
def retry(request, job_id):
    job = owned_job(request, job_id)
    updated = Job.objects.filter(pk=job.pk, status=Job.Status.FAILED).update(
        status=Job.Status.QUEUED, stage="queue", progress=0, error="", activity="Retry queued",
        events=[*job.events[-39:], {"time": timezone.now().isoformat(), "stage": "queue",
                                   "message": "Retry queued", "level": "info"}],
        updated_at=timezone.now(),
    )
    if not updated:
        return JsonResponse({"error": "Only failed renders can be retried."}, status=409)
    job.refresh_from_db()
    return JsonResponse(render_data(job))


@require_POST
@never_cache
def edit_clip(request, job_id, index):
    job = owned_job(request, job_id)
    if job.status != Job.Status.COMPLETE or index >= len(job.clips):
        raise Http404
    data, error = form_data(request, ClipTextForm)
    if error is not None:
        return error
    with FileLock(job_directory(job.id) / "metadata.lock"):
        job.refresh_from_db(fields=["clips"])
        job.clips[index].update(data)
        job.save(update_fields=["clips", "updated_at"])
    return JsonResponse(render_data(job))


@require_safe
@never_cache
def archive(request, job_id):
    job = owned_job(request, job_id)
    if job.status != Job.Status.COMPLETE or not job.clips:
        raise Http404
    try:
        handle = open_archive(job.id, job.clips)
    except FileNotFoundError as exc:
        raise Http404 from exc
    source = job.video_id or slugify(Path(job.source_name).stem, allow_unicode=True)[:64].strip("-") or str(job.id)[:8]
    return FileResponse(handle, as_attachment=True, filename=f"cutframe-{source}.zip")


def stream_range(path, start, remaining):
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining:
            chunk = handle.read(min(64 * 1024, remaining))
            if not chunk:
                break
            yield chunk
            remaining -= len(chunk)


def range_response(request, path):
    size = path.stat().st_size
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(size)}
    requested = request.headers.get("Range")
    if not requested:
        if request.method == "HEAD":
            return HttpResponse(content_type="video/mp4", headers=headers)
        return FileResponse(path.open("rb"), content_type="video/mp4", headers=headers)

    match = re.fullmatch(r"bytes=([0-9]{0,19})-([0-9]{0,19})", requested)
    if match and any(match.groups()):
        first, last = match.groups()
        start = int(first) if first else max(0, size - int(last))
        end = min(int(last), size - 1) if first and last else size - 1
        if start <= end:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            headers["Content-Length"] = str(end - start + 1)
            if request.method == "HEAD":
                return HttpResponse(status=206, content_type="video/mp4", headers=headers)
            return StreamingHttpResponse(
                stream_range(path, start, end - start + 1), status=206,
                content_type="video/mp4", headers=headers,
            )
    return HttpResponse(status=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})


@require_safe
@never_cache
def media(request, job_id, index, kind):
    job = owned_job(request, job_id)
    suffixes = {"video": ".mp4", "thumbnail": ".jpg", "text": ".txt", "captions": ".srt"}
    if job.status != Job.Status.COMPLETE or index >= len(job.clips) or kind not in suffixes:
        raise Http404
    clip = job.clips[index]
    download = request.GET.get("download") == "1"
    title_based = publication_filename(clip["title"]) + suffixes[kind]
    if kind == "text":
        return FileResponse(BytesIO(clip_text(clip)), as_attachment=True, filename=title_based)
    if kind == "captions" and not clip.get("has_captions"):
        raise Http404
    filename = clip["name"] + suffixes[kind]
    path = job_directory(job.id) / "files" / filename
    if not path.is_file():
        raise Http404
    if kind == "video":
        if download:
            handle = open_published_video(job_directory(job.id), clip)
            return FileResponse(handle, as_attachment=True, filename=title_based, content_type="video/mp4")
        return range_response(request, path)
    if kind == "captions":
        return FileResponse(path.open("rb"), as_attachment=True, filename=title_based, content_type="application/x-subrip")
    return FileResponse(path.open("rb"), as_attachment=download, filename=title_based)
