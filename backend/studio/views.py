import json
import re
from functools import wraps
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods, require_safe
from filelock import FileLock, Timeout

from shorts_generator.local.llm import RankingConfigurationError, RankingModelUnavailable, check_connection, list_ranking_models
from shorts_generator.local.cover_design import CoverDesignError, design_cover
from shorts_generator.local.thumbnails import create_thumbnail_set

from .ai_settings import (
    AISettingsForm, ModelDiscoveryForm, TranscriptionSettingsForm, ai_settings_data,
    installation_ai, installation_options, merge_ai_options, merge_installation, resolve_ai_options, saved_ai_options,
)
from .descriptions import (DescriptionDiscoveryForm, DescriptionDraftForm, DescriptionError,
                           DescriptionSettingsForm, connection_info, generate_descriptions,
                           saved_description_options, subtitle_excerpt, writer_config)
from .exports import (clip_text, job_directory, open_archive, open_published_video, publication_filename, render_lock,
                      save_thumbnail_content, set_ai_thumbnail, store_thumbnail_options, thumbnail_filename,
                      thumbnail_reference)
from .forms import AlternateRenderForm, ClipTextForm, JobForm, PreferencesForm, StorageSettingsForm, ThumbnailDesignForm, UploadJobForm
from .models import (InstallationSettings, PREFERENCE_FIELDS, RENDER_OPTIONS, Job, StudioMode,
                     WorkspaceAISettings, WorkspacePreferences, WorkspaceThumbnailSettings)
from .thumbnail_ai import (DEFAULTS as THUMBNAIL_DEFAULTS, ThumbnailAISettingsForm, ThumbnailGenerationError,
                           ThumbnailDiscoveryForm, ThumbnailGenerationForm, image_provider, list_image_models,
                           make_ai_cover, merge_thumbnail_ai,
                           saved_thumbnail_ai, thumbnail_settings_data)
from .uploads import MIB, VIDEO_EXTENSIONS, source_available, store_upload, upload_limit


def render_data(job, *, include_activity=True):
    clips = []
    for index, clip in enumerate(job.clips):
        media = {
            f"{kind}_url": reverse("media", args=[job.id, index, kind])
            for kind in ("video", "thumbnail", "text")
        }
        media["edit_url"] = reverse("edit_clip", args=[job.id, index])
        if clip.get("thumbnail_file"):
            media["thumbnail_url"] += f"?v={clip['thumbnail_file']}"
        if include_activity:
            media["description_draft_url"] = reverse("draft_description", args=[job.id, index])
            media["thumbnail_editor_url"] = reverse("edit_thumbnail", args=[job.id, index, "frames"])
            media["thumbnail_options"] = [
                {"time": choice["time"], "url": reverse("media", args=[job.id, index, f"thumbnail-frame-{number}"])}
                for number, choice in enumerate(clip.get("thumbnail_options", []))
            ]
            for action in ("save", "preview", "generate"):
                media[f"thumbnail_{action}_url"] = reverse("edit_thumbnail", args=[job.id, index, action])
        if clip.get("has_captions"):
            media["captions_url"] = reverse("media", args=[job.id, index, "captions"])
        clips.append({key: value for key, value in clip.items()
                      if key not in {"name", "embedded_title", "thumbnail_file", "thumbnail_options", "description_context"}} | media)
    return {
        "id": str(job.id), "video_id": job.video_id, "source_url": job.source_url,
        "source_name": job.source_name, "source_type": "upload" if job.source_name else "youtube",
        "studio_mode": job.studio_mode, "highlight_minutes": job.highlight_minutes, "highlight_method": job.highlight_method,
        "editing_style": job.editing_style,
        "selection_mode": job.selection_mode, "min_viral_score": job.min_viral_score,
        "clip_count": job.clip_count, "aspect_ratio": job.aspect_ratio, "quality": job.quality,
        "framing": job.framing, "caption_style": job.caption_style, "language": job.language,
        "min_clip_seconds": job.min_clip_seconds, "max_clip_seconds": job.max_clip_seconds,
        "status": job.status, "stage": job.stage, "progress": job.progress,
        "created_at": job.created_at.isoformat(), "error": job.error, "clips": clips,
        "activity": job.activity,
        "skip_ai_covers": job.skip_ai_covers,
        **({"events": job.events, "can_reuse_source": source_available(job),
            "source_render_id": str(job.source_render_id) if job.source_render_id else None} if include_activity else {}),
        "archive_url": reverse("archive", args=[job.id]) if job.status == Job.Status.COMPLETE and clips else None,
    }


def owned_job(request, job_id):
    owner = request.session.get("studio_owner")
    if not owner:
        raise Http404
    return get_object_or_404(Job, pk=job_id, owner=owner)


def render_operation(view):
    @wraps(view)
    def perform(request, job_id, *args, **kwargs):
        job = owned_job(request, job_id)
        try:
            with render_lock(job.id):
                return view(request, owned_job(request, job_id), *args, **kwargs)
        except Timeout:
            return JsonResponse({"error": "This render is busy. Try again when its current operation finishes."}, status=409)
        except FileNotFoundError as exc:
            raise Http404 from exc
    return perform


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


def form_data(request, form_class, defaults=None, **form_kwargs):
    if request.content_type != "application/json":
        return None, JsonResponse({"error": "Send the form as JSON."}, status=415)
    try:
        payload = json.loads(request.body)
    except ValueError:
        return None, JsonResponse({"error": "The request could not be read."}, status=400)
    if not isinstance(payload, dict):
        return None, JsonResponse({"error": "Send a form object."}, status=400)
    form = form_class((defaults or {}) | payload, **form_kwargs)
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
    global_scope = request.GET.get("scope") == "installation"
    transcription = request.GET.get("task") == "transcription"
    if global_scope and request.method == "POST":
        with transaction.atomic():
            row, _ = InstallationSettings.objects.select_for_update().get_or_create(pk=1)
            if transcription:
                data, error = form_data(request, TranscriptionSettingsForm, defaults=row.ai)
            else:
                data, error = form_data(request, AISettingsForm, defaults=installation_options(row.ai), installation=row.ai)
            if error is not None:
                return error
            row.ai = row.ai | data if transcription else merge_installation(row.ai, data)
            row.save(update_fields=["ai"])
        return JsonResponse(ai_settings_data(installation_options(row.ai), row.ai, global_scope=True))

    installation = installation_ai()
    if global_scope:
        return JsonResponse(ai_settings_data(installation_options(installation), installation, global_scope=True))
    owner = request.session.get("studio_owner")
    options = saved_ai_options(owner)
    if request.method == "POST":
        with transaction.atomic():
            if owner:
                row = WorkspaceAISettings.objects.select_for_update().filter(pk=owner).first()
                options = row.config if row else {}
            if transcription:
                data, error = form_data(request, TranscriptionSettingsForm, defaults=options)
            else:
                data, error = form_data(request, AISettingsForm, defaults=options, installation=installation)
            if error is not None:
                return error
            options = options | data if transcription else merge_ai_options(data, options)
            WorkspaceAISettings.objects.update_or_create(owner=ensure_owner(request), defaults={"config": options})
    return JsonResponse(ai_settings_data(options, installation))


@require_POST
@never_cache
def ai_connection(request, *, discover=False):
    installation = installation_ai()
    global_scope = request.GET.get("scope") == "installation"
    descriptions = request.GET.get("task") == "descriptions"
    owner = request.session.get("studio_owner")
    ranking = saved_ai_options(owner)
    if descriptions:
        saved = saved_description_options(owner)
        form = DescriptionDiscoveryForm if discover else DescriptionSettingsForm
    else:
        saved = installation_options(installation) if global_scope else ranking
        form = ModelDiscoveryForm if discover else AISettingsForm
    data, error = form_data(request, form, defaults=saved, installation=installation)
    if error is not None:
        return error
    try:
        if descriptions:
            config = writer_config(merge_ai_options(data, saved), ranking, installation)
        elif global_scope:
            installation = merge_installation(installation, data)
            config = resolve_ai_options(installation_options(installation), installation)
        else:
            config = resolve_ai_options(merge_ai_options(data, saved), installation)
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


@require_http_methods(["GET", "POST"])
@never_cache
def description_settings(request):
    owner = request.session.get("studio_owner")
    options = saved_description_options(owner)
    installation = installation_ai()
    if request.method == "POST":
        with transaction.atomic():
            if owner:
                WorkspaceAISettings.objects.select_for_update().filter(pk=owner).first()
                options = saved_description_options(owner)
            data, error = form_data(request, DescriptionSettingsForm, defaults=options, installation=installation)
            if error is not None:
                return error
            updated = merge_ai_options(data, options)
            profile = {key: updated[key] for key in ("provider", "base_url", "secret") if key in updated}
            owner = ensure_owner(request)
            WorkspaceAISettings.objects.update_or_create(owner=owner, defaults={
                "description_model": data["model"], "description_automatic": data["automatic"],
                "description_connection": profile if data["provider"] else {},
            })
            options = profile | {"model": data["model"], "automatic": data["automatic"]}
    connection = None
    try:
        config = writer_config(options, saved_ai_options(owner), installation)
        config.validate()
        connection = connection_info(config)
    except RankingConfigurationError:
        pass
    return JsonResponse({"settings": {key: options[key] for key in ("provider", "model", "base_url", "automatic")},
                         "connection": connection, "has_key": bool(options.get("secret"))})


@require_http_methods(["GET", "POST"])
@never_cache
def thumbnail_ai_settings(request):
    owner = request.session.get("studio_owner")
    options = saved_thumbnail_ai(owner)
    if request.method == "POST":
        data, error = form_data(request, ThumbnailAISettingsForm, defaults=THUMBNAIL_DEFAULTS | options)
        if error is not None:
            return error
        owner = ensure_owner(request)
        options = merge_thumbnail_ai(data, options)
        WorkspaceThumbnailSettings.objects.update_or_create(owner=owner, defaults={"config": options})
    return JsonResponse(thumbnail_settings_data(owner, options))


@require_POST
@never_cache
def thumbnail_ai_connection(request, *, discover=False):
    owner = request.session.get("studio_owner")
    saved = saved_thumbnail_ai(owner)
    form = ThumbnailDiscoveryForm if discover else ThumbnailAISettingsForm
    data, error = form_data(request, form, defaults=THUMBNAIL_DEFAULTS | saved)
    if error is not None:
        return error
    if not data["provider"]:
        return JsonResponse({"error": "Choose an image provider first."}, status=400)
    try:
        provider = image_provider(owner, merge_thumbnail_ai(data, saved))
        models = list_image_models(provider)
        if discover:
            return JsonResponse({"models": models})
        if provider.model not in models:
            raise ThumbnailGenerationError("The selected image model is missing from the server’s model list.")
    except ThumbnailGenerationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"message": "The selected model is visible to Cutframe."})


@require_http_methods(["GET", "POST"])
@never_cache
def storage_settings(request):
    if request.method == "POST":
        data, error = form_data(request, StorageSettingsForm)
        if error is not None:
            return error
        InstallationSettings.objects.update_or_create(pk=1, defaults={"max_upload_bytes": data["max_upload_mib"] * MIB})
    maximum = upload_limit()
    return JsonResponse({"max_upload_bytes": maximum, "max_upload_mib": max(1, maximum // MIB)})


@never_cache
def jobs(request):
    owner = request.session.get("studio_owner")
    if request.method == "GET":
        renders = Job.objects.filter(owner=owner) if owner else Job.objects.none()
        if request.GET.get("finished") == "1":
            renders = renders.filter(status=Job.Status.COMPLETE).exclude(clips=[])
        else:
            renders = renders.exclude(status=Job.Status.DELETING)[:50]
        return JsonResponse({
            "jobs": [render_data(job, include_activity=False) for job in renders],
            "upload": {"max_bytes": upload_limit(), "extensions": VIDEO_EXTENSIONS},
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
    form = UploadJobForm(defaults | request.POST.dict(), request.FILES,
                         max_upload_bytes=getattr(request, "studio_upload_limit", None))
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
@render_operation
def create_alternate(request, job):
    if not source_available(job):
        return JsonResponse({"error": "The original video for this render is unavailable."}, status=409)
    mode = StudioMode.alternate(job.studio_mode)
    defaults = preference_values(saved_preferences(job.owner), RENDER_OPTIONS) | {
        "studio_mode": mode, "quality": job.quality, "language": job.language, "caption_style": job.caption_style,
    }
    data, error = form_data(request, AlternateRenderForm, defaults=defaults)
    if error is not None:
        return error
    if data["studio_mode"] != mode:
        return JsonResponse({"error": "Choose the other output type for this source."}, status=400)
    alternate = Job.objects.create(owner=job.owner, source_render=job, source_url=job.source_url,
                                   source_name=job.source_name, video_id=job.video_id, **data)
    return JsonResponse(render_data(alternate), status=201)


@require_POST
@never_cache
@render_operation
def cancel_render(request, job):
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job.pk)
        if job.status in (Job.Status.CANCELLING, Job.Status.CANCELLED):
            return JsonResponse(render_data(job))
        if job.status not in Job.ACTIVE:
            return JsonResponse({"error": "Only queued or running renders can be cancelled."}, status=409)
        queued = job.status == Job.Status.QUEUED
        job.status = Job.Status.CANCELLED if queued else Job.Status.CANCELLING
        if queued:
            job.stage = "cancelled"
        job.activity = "Render cancelled before it started." if queued else "Stopping after the current operation."
        job.events = [*job.events[-39:], {"time": timezone.now().isoformat(), "stage": job.stage,
                     "message": job.activity, "level": "info"}]
        job.save(update_fields=["status", "stage", "activity", "events", "updated_at"])
    return JsonResponse(render_data(job))


@require_POST
@never_cache
@render_operation
def skip_ai_covers(request, job):
    if not Job.objects.filter(pk=job.pk, status=Job.Status.RUNNING, stage__in=("covers", "export")).update(
        skip_ai_covers=True, updated_at=timezone.now(),
    ):
        return JsonResponse({"error": "AI cover generation is no longer in progress."}, status=409)
    job.refresh_from_db()
    return JsonResponse(render_data(job))


@require_POST
@never_cache
@render_operation
def retry(request, job):
    updated = Job.objects.filter(pk=job.pk, status__in=(Job.Status.FAILED, Job.Status.CANCELLED)).update(
        status=Job.Status.QUEUED, stage="queue", progress=0, error="", activity="Retry queued", skip_ai_covers=False,
        events=[*job.events[-39:], {"time": timezone.now().isoformat(), "stage": "queue",
                                   "message": "Retry queued", "level": "info"}],
        updated_at=timezone.now(),
    )
    if not updated:
        return JsonResponse({"error": "Only failed or cancelled renders can be retried."}, status=409)
    job.refresh_from_db()
    return JsonResponse(render_data(job))


@require_POST
@never_cache
@render_operation
def edit_clip(request, job, index):
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


@require_POST
@never_cache
def draft_description(request, job_id, index):
    job = owned_job(request, job_id)
    if job.status != Job.Status.COMPLETE or index >= len(job.clips):
        raise Http404
    data, error = form_data(request, DescriptionDraftForm)
    if error is not None:
        return error
    try:
        config = writer_config(saved_description_options(job.owner), saved_ai_options(job.owner))
        config.validate()
    except RankingConfigurationError:
        return JsonResponse({"error": "Connect a text provider in Settings → AI models, then choose a description model."}, status=422)
    clip = dict(job.clips[index])
    if not clip.get("description_context") and clip.get("has_captions"):
        captions = job_directory(job.id) / "files" / f"{clip['name']}.srt"
        clip["description_context"] = subtitle_excerpt(captions)
    for field in ("title", "description"):
        if data[field]:
            clip[field] = data[field]
    try:
        drafts = generate_descriptions(config, [clip], platform=data["platform"] or "social", language=job.language)
    except DescriptionError as exc:
        return JsonResponse({"error": str(exc)}, status=422)
    return JsonResponse({"description": drafts[0], "model": config.model})


@require_POST
@never_cache
@render_operation
def edit_thumbnail(request, job, index, action):
    if job.status != Job.Status.COMPLETE or index >= len(job.clips):
        raise Http404
    if action == "generate":
        return generate_saved_thumbnail(request, job, index)
    if action not in {"frames", "preview", "save"}:
        raise Http404
    directory = job_directory(job.id)
    files = directory / "files"
    with FileLock(directory / "metadata.lock"):
        job.refresh_from_db(fields=["clips"])
        clip = job.clips[index]
        choices = clip.get("thumbnail_options", [])
        if action == "frames":
            if choices:
                return JsonResponse(render_data(job))
            video = files / f"{clip['name']}.mp4"
            if not video.is_file():
                raise Http404
            with TemporaryDirectory(prefix=".thumbnail-", dir=directory) as staging:
                work = Path(staging).resolve()
                try:
                    choices, _ = create_thumbnail_set(video, work / "cover.jpg", aspect_ratio=job.aspect_ratio,
                                                      main_screen=job.framing == "stream" and job.aspect_ratio == "9:16")
                except (OSError, RuntimeError):
                    return JsonResponse({"error": "Cutframe could not read frames from this render."}, status=422)
                clip["thumbnail_options"] = store_thumbnail_options(files, clip["name"], choices, work)
        else:
            data, error = form_data(request, ThumbnailDesignForm)
            if error is not None:
                return error
            if data["frame"] >= len(choices):
                return JsonResponse({"error": "Choose an available thumbnail frame."}, status=400)
            choice = choices[data["frame"]]
            placement = choice["placement"] if data["placement"] == "auto" else data["placement"]
            try:
                cover = design_cover(files / choice["file"], headline=data["headline"], accent=data["accent"],
                                     placement=placement)
            except CoverDesignError as exc:
                return JsonResponse({"error": str(exc)}, status=400)
            except OSError:
                return JsonResponse({"error": "The thumbnail frame is unavailable."}, status=404)
            if action == "preview":
                return HttpResponse(cover, content_type="image/jpeg")
            clip["thumbnail_file"] = save_thumbnail_content(files, clip["name"], cover)
            clip["thumbnail_design"] = data
            clip.pop("thumbnail_model", None)
            clip.pop("thumbnail_source", None)
        job.save(update_fields=["clips", "updated_at"])
    return JsonResponse(render_data(job))


def generate_saved_thumbnail(request, job, index):
    data, error = form_data(request, ThumbnailGenerationForm)
    if error is not None:
        return error
    options = saved_thumbnail_ai(job.owner)
    if not options.get("provider"):
        return JsonResponse({"error": "Choose an image model in Settings → Thumbnails first."}, status=400)
    directory = job_directory(job.id)
    files = directory / "files"
    if not files.is_dir():
        raise Http404
    try:
        with FileLock(directory / f"generate-thumbnail-{index}.lock", timeout=0):
            job.refresh_from_db(fields=["clips"])
            clip = job.clips[index]
            provider = image_provider(job.owner, options)
            cover = make_ai_cover(provider, clip, job.aspect_ratio, directory,
                                  reference=thumbnail_reference(files, clip), direction=data["direction"])
            with FileLock(directory / "metadata.lock"):
                job.refresh_from_db(fields=["clips"])
                current = job.clips[index]
                if current != clip:
                    return JsonResponse({"error": "This clip changed during generation. Refresh and try again."}, status=409)
                set_ai_thumbnail(files, current, cover, provider.model)
                job.save(update_fields=["clips", "updated_at"])
    except Timeout:
        return JsonResponse({"error": "A thumbnail is already being generated for this clip."}, status=409)
    except ThumbnailGenerationError as exc:
        return JsonResponse({"error": str(exc)}, status=422)
    return JsonResponse(render_data(job))


@require_safe
@never_cache
@render_operation
def archive(request, job):
    if job.status != Job.Status.COMPLETE or not job.clips:
        raise Http404
    try:
        handle = open_archive(job.id, job.clips)
    except FileNotFoundError as exc:
        raise Http404 from exc
    source = job.video_id or slugify(Path(job.source_name).stem, allow_unicode=True)[:64].strip("-") or str(job.id)[:8]
    prefix = "cutframe-highlights" if job.studio_mode == StudioMode.HIGHLIGHTS else "cutframe"
    return FileResponse(handle, as_attachment=True, filename=f"{prefix}-{source}.zip")


class VideoByteRange:
    def __init__(self, path, start, length):
        self.handle = path.open("rb")
        self.handle.seek(start)
        self.remaining = length

    def __iter__(self):
        while self.remaining:
            chunk = self.handle.read(min(64 * 1024, self.remaining))
            if not chunk:
                return
            self.remaining -= len(chunk)
            yield chunk

    def close(self):
        self.handle.close()


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
                VideoByteRange(path, start, end - start + 1), status=206,
                content_type="video/mp4", headers=headers,
            )
    return HttpResponse(status=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})


@require_safe
@never_cache
def media(request, job_id, index, kind):
    if kind == "video" and request.GET.get("download") == "1":
        return download_video(request, job_id, index)
    try:
        return render_media(request, owned_job(request, job_id), index, kind)
    except FileNotFoundError as exc:
        raise Http404 from exc


@render_operation
def download_video(request, job, index):
    return render_media(request, job, index, "video")


def render_media(request, job, index, kind):
    suffixes = {"video": ".mp4", "thumbnail": ".jpg", "text": ".txt", "captions": ".srt"}
    if job.status != Job.Status.COMPLETE or index >= len(job.clips):
        raise Http404
    clip = job.clips[index]
    download = request.GET.get("download") == "1"
    frame = re.fullmatch(r"thumbnail-frame-([0-2])", kind)
    if frame:
        choices = clip.get("thumbnail_options", [])
        number = int(frame[1])
        if number >= len(choices):
            raise Http404
        path = job_directory(job.id) / "files" / choices[number]["file"]
        if not path.is_file():
            raise Http404
        title = f"{publication_filename(clip['title'])} - Frame {number + 1}.jpg"
        return FileResponse(path.open("rb"), as_attachment=download, filename=title, content_type="image/jpeg")
    if kind not in suffixes:
        raise Http404
    title_based = publication_filename(clip["title"]) + suffixes[kind]
    if kind == "text":
        return FileResponse(BytesIO(clip_text(clip)), as_attachment=True, filename=title_based)
    if kind == "captions" and not clip.get("has_captions"):
        raise Http404
    filename = thumbnail_filename(clip) if kind == "thumbnail" else clip["name"] + suffixes[kind]
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
