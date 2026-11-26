import json
import secrets
from datetime import timedelta
from urllib.parse import urlencode
from uuid import UUID

from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.http.request import UnreadablePostError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from filelock import Timeout

from studio.exports import render_lock
from studio.models import Job
from studio.views import ensure_owner, form_data

from .config import PublishError, client_config, publishing_settings, seal
from .forms import (MAX_BATCH_POSTS, BulkPublishForm, FinishForm, InstagramAppForm, NetworkSettingsForm,
                    PlatformAppForm, PublishForm)
from .models import Authorization, Connection, Platform, Publication, PublicationBatch, PublishingSettings
from .oauth import authorize_url, exchange, state_digest
from .queue import QueueError, destination_account, prepare_post, queue_batch
from .thumbnails import cover_options, youtube_video_id


Status = Publication.Status
BULK_BODY_LIMIT = 2 * 1024**2
QUEUE_PAGE_SIZE = 20


def fail(message, status=400):
    return JsonResponse({"error": message}, status=status)


def publication_data(post):
    return {
        "id": post.id, "job_id": post.job_id, "clip_index": post.clip_index, "platform": post.platform, "batch_id": post.batch_id,
        "account": post.account_label, "title": post.options["title"], "caption": post.options["caption"],
        "status": post.status, "progress": post.progress,
        "scheduled_at": post.scheduled_at, "started_at": post.started_at, "message": post.message, "url": post.url,
        "thumbnail_state": post.options.get("thumbnail_state", ""),
        "can_apply_thumbnail": (bool(post.job_id) and post.platform == Platform.YOUTUBE and post.status == Status.COMPLETE
                                and bool(youtube_video_id(post.url))),
    }


def settings_data(setup):
    platforms = []
    for platform in Platform.values:
        config = client_config(platform, setup)
        platforms.append({"platform": platform, "name": Platform(platform).label,
                          "client_id": config.client_id, "has_secret": bool(config.secret),
                          "redirect_uri": config.redirect_uri})
    return {
        "studio_origin": setup.studio_origin, "callback_origin": setup.callback_origin, "platforms": platforms,
        "instagram_login_config": setup.instagram_login_config, "meta_api_version": setup.meta_api_version,
    }


@require_GET
@never_cache
def platform_settings(request):
    return JsonResponse(settings_data(publishing_settings()))


@require_POST
@never_cache
def save_platform_settings(request, section):
    forms = {"network": NetworkSettingsForm, Platform.YOUTUBE: PlatformAppForm,
             Platform.TIKTOK: PlatformAppForm, Platform.INSTAGRAM: InstagramAppForm}
    if section not in forms:
        raise Http404
    with transaction.atomic():
        setup, _ = PublishingSettings.objects.select_for_update().get_or_create(pk=1)
        defaults = {}
        if section == Platform.INSTAGRAM:
            defaults = {"meta_api_version": setup.meta_api_version, "instagram_login_config": setup.instagram_login_config}
        data, error = form_data(request, forms[section], defaults=defaults)
        if error is not None:
            return error
        if section == "network":
            setup.studio_origin = data["studio_origin"]
            setup.callback_origin = data["callback_origin"]
            fields = ["studio_origin", "callback_origin"]
        else:
            old = setup.clients.get(section, {})
            client_id = data["client_id"]
            secret = ""
            if not data["remove_secret"]:
                if data["client_secret"]:
                    secret = seal({"value": data["client_secret"]})
                elif old.get("id") == client_id:
                    secret = old.get("secret", "")
            setup.clients[section] = {"id": client_id, "secret": secret}
            fields = ["clients"]
            if section == Platform.INSTAGRAM:
                setup.instagram_login_config = data["instagram_login_config"]
                setup.meta_api_version = data["meta_api_version"]
                fields += ["instagram_login_config", "meta_api_version"]
        setup.save(update_fields=fields)
    return JsonResponse(settings_data(setup))


@require_GET
@never_cache
def accounts(request):
    owner = request.session.get("studio_owner")
    connections = {row.platform: row for row in Connection.objects.filter(owner=owner)} if owner else {}
    setup = publishing_settings()
    platforms = []
    for platform in Platform.values:
        config = client_config(platform, setup)
        connected = connections.get(platform)
        platforms.append({
            "platform": platform, "name": Platform(platform).label,
            "credentials_ready": bool(config.client_id and config.secret),
            "configured": bool(config.client_id and config.secret and config.redirect_uri),
            "redirect_uri": config.redirect_uri,
            "accounts": connected.accounts if connected and connected.client_id == config.client_id else [],
        })
    return JsonResponse({"origin": setup.studio_origin, "platforms": platforms})


@require_POST
@never_cache
def connect(request, platform):
    if platform not in Platform.values:
        raise Http404
    setup = publishing_settings()
    config = client_config(platform, setup)
    current_origin = setup.studio_origin
    if not current_origin or not config.client_id or not config.secret or not config.redirect_uri:
        return fail("Complete this platform's server configuration before connecting.")
    if request.headers.get("Origin") and request.headers["Origin"] != current_origin:
        return fail(f"Open Cutframe at {current_origin} to connect an account from this browser.")
    state = secrets.token_urlsafe(32)
    expires = timezone.now() + timedelta(minutes=10)
    verifier = secrets.token_urlsafe(48)
    try:
        url = authorize_url(platform, config, state, verifier)
    except PublishError as exc:
        return fail(str(exc))
    owner = ensure_owner(request)
    Authorization.objects.filter(expires_at__lte=timezone.now()).delete()
    Authorization.objects.filter(owner=owner, platform=platform).delete()
    Authorization.objects.create(state=state_digest(state), owner=owner, platform=platform,
                                 redirect_uri=config.redirect_uri, client_id=config.client_id,
                                 verifier=verifier, expires_at=expires)
    return JsonResponse({"url": url})


@require_GET
@never_cache
def callback(request, platform):
    if platform not in Platform.values:
        raise Http404
    current_origin = publishing_settings().studio_origin
    state, code = request.GET.get("state", ""), request.GET.get("code", "")
    valid = bool(state and len(state) <= 128 and Authorization.objects.filter(
        state=state_digest(state), platform=platform, expires_at__gt=timezone.now(),
    ).exists())
    keys = {"provider": platform, "state": state, "code": code} if valid and 0 < len(code) <= 2048 else {
        "connection": "cancelled" if valid and request.GET.get("error") else "expired",
    }
    response = HttpResponseRedirect(f"{current_origin}/publish#{urlencode(keys)}")
    response["Referrer-Policy"] = "no-referrer"
    return response


@require_POST
@never_cache
def finish(request):
    data, error = form_data(request, FinishForm)
    if error is not None:
        return error
    platform = data["platform"]
    owner = request.session.get("studio_owner")
    if not owner:
        return fail("Open Publish Studio in the browser that started the connection.")
    config = client_config(platform)
    with transaction.atomic():
        authorization = Authorization.objects.select_for_update().filter(
            state=state_digest(data["state"]), platform=platform, owner=owner, expires_at__gt=timezone.now(),
        ).first()
        if (not authorization or not config.secret or config.redirect_uri != authorization.redirect_uri
                or config.client_id != authorization.client_id):
            return fail("This connection expired. Start again from Publish Studio.")
        authorization.delete()
    try:
        accounts, credentials = exchange(platform, config, data["code"], authorization.verifier)
    except PublishError as exc:
        return fail(str(exc))
    except (KeyError, TypeError, ValueError):
        return fail("The platform sent an incomplete authorization. Check its permissions and try again.")
    Connection.objects.update_or_create(owner=owner, platform=platform, defaults={
        "client_id": config.client_id, "accounts": accounts, "credentials": seal(credentials),
    })
    return JsonResponse({"connected": Platform(platform).label})


@require_POST
@never_cache
def disconnect(request, platform):
    if platform not in Platform.values:
        raise Http404
    owner = request.session.get("studio_owner")
    with transaction.atomic():
        if Publication.objects.filter(owner=owner, platform=platform, status=Status.SENDING).exists():
            return fail("Wait for the current transfer to finish before disconnecting.", status=409)
        waiting = Publication.objects.filter(owner=owner, platform=platform, status=Status.QUEUED, started_at__isnull=True)
        waiting.update(status=Status.CANCELLED, message="Account disconnected before sending.", updated_at=timezone.now())
        Publication.objects.filter(owner=owner, platform=platform, status=Status.PROCESSING).update(
            status=Status.REVIEW, message="Account disconnected after transfer began. Check the destination platform.",
            updated_at=timezone.now(),
        )
        Connection.objects.filter(owner=owner, platform=platform).delete()
        Authorization.objects.filter(owner=owner, platform=platform).delete()
    return JsonResponse({"disconnected": True})


@require_http_methods(["GET", "POST"])
@never_cache
def posts(request):
    owner = request.session.get("studio_owner")
    if request.method == "GET":
        history = Publication.objects.filter(owner=owner).order_by("-created_at", "id")
        batch_id = request.GET.get("batch")
        if batch_id:
            try:
                batch_id = UUID(batch_id)
            except ValueError as exc:
                raise Http404 from exc
            get_object_or_404(PublicationBatch, pk=batch_id, owner=owner)
            history = history.filter(batch_id=batch_id).order_by("scheduled_at", "created_at", "id")
        platform = request.GET.get("platform")
        if platform in Platform.values:
            history = history.filter(platform=platform)
        status = request.GET.get("status")
        if status == "active":
            history = history.filter(status__in=Publication.ACTIVE)
        elif status == "attention":
            history = history.filter(status__in=(Status.FAILED, Status.REVIEW))
        elif status in Status.values:
            history = history.filter(status=status)
        requested = request.GET.get("page")
        if requested is not None:
            valid_page = 0 < len(requested) < 10 and requested.isascii() and requested.isdecimal()
            number = max(1, int(requested)) if valid_page else 1
            page = Paginator(history, QUEUE_PAGE_SIZE).get_page(number)
            return JsonResponse({"posts": [publication_data(post) for post in page], "pagination": {
                "page": page.number, "pages": page.paginator.num_pages, "total": page.paginator.count,
                "start": page.start_index(), "end": page.end_index(),
            }})
        # Preserve the existing feed for consumers that do not request a page.
        limit = MAX_BATCH_POSTS if batch_id else 100
        return JsonResponse({"posts": [publication_data(post) for post in history[:limit]]})
    data, error = form_data(request, PublishForm)
    if error is not None:
        return error
    if not owner:
        raise Http404
    job = get_object_or_404(Job, pk=data["job_id"], owner=owner, status=Job.Status.COMPLETE)
    try:
        with render_lock(job.id):
            job = get_object_or_404(Job, pk=job.pk, owner=owner, status=Job.Status.COMPLETE)
            return queue_post(owner, job, data)
    except Timeout:
        return fail("This render is in use. Try again in a moment.", status=409)
    except QueueError as exc:
        return fail(str(exc), status=exc.status)


def queue_post(owner, job, data):
    platform = data["platform"]
    connection = Connection.objects.filter(owner=owner, platform=platform).first()
    account = destination_account(connection, client_config(platform), data["account_id"])
    fields = prepare_post(job, data, account, data["scheduled_at"] or timezone.now())
    post, created = Publication.objects.get_or_create(pk=data["request_id"], defaults=fields)
    if post.owner != job.owner or post.job_id != job.id:
        return fail("This request identifier is unavailable.", status=409)
    return JsonResponse(publication_data(post), status=201 if created else 200)


@require_POST
@never_cache
def bulk_posts(request):
    owner = request.session.get("studio_owner")
    if not owner:
        raise Http404
    if request.content_type != "application/json":
        return fail("Send the batch as JSON.", status=415)
    # The batch can include many captions. Keep the larger limit scoped to this endpoint.
    try:
        raw = request.read(BULK_BODY_LIMIT + 1)
        if len(raw) > BULK_BODY_LIMIT:
            return fail("This batch is too large. Choose fewer videos or shorten the captions.", status=413)
        payload = json.loads(raw)
    except (ValueError, RecursionError, UnreadablePostError):
        return fail("The batch could not be read.")
    if not isinstance(payload, dict):
        return fail("Send a batch object.")
    form = BulkPublishForm(payload)
    if not form.is_valid():
        return JsonResponse({"fields": form.errors.get_json_data()}, status=400)
    try:
        batch, queued, created = queue_batch(owner, form.cleaned_data)
    except QueueError as exc:
        return fail(str(exc), status=exc.status)
    return JsonResponse({"batch_id": batch.id, "posts": [publication_data(post) for post in queued]}, status=201 if created else 200)


@require_POST
@never_cache
def post_action(request, post_id, action):
    owner = request.session.get("studio_owner")
    record = Publication.objects.filter(owner=owner, pk=post_id)
    post = record.first()
    if post is None:
        raise Http404
    if post.job_id is None:
        return fail("The local render was deleted.", status=409)
    try:
        with render_lock(post.job_id):
            record = record.filter(job_id=post.job_id, job__status=Job.Status.COMPLETE)
            return change_post(record, action)
    except Timeout:
        return fail("This render is in use. Try again in a moment.", status=409)


def change_post(record, action):
    if action == "thumbnail":
        return queue_existing_thumbnail(record)
    if action == "cancel":
        changed = record.filter(status=Status.QUEUED, started_at__isnull=True).update(
            status=Status.CANCELLED, message="Cancelled before sending.", updated_at=timezone.now(),
        )
    elif action in {"retry", "check"}:
        changed = record.filter(status=Status.FAILED if action == "retry" else Status.INBOX).update(
            status=Status.PROCESSING, next_at=timezone.now(), started_at=None, attempts=0,
            message="Queued for a platform status check.", updated_at=timezone.now(),
        )
    else:
        raise Http404
    if not changed:
        return fail("That action is no longer available for this post.", status=409)
    return JsonResponse(publication_data(record.get()))


def queue_existing_thumbnail(record):
    with transaction.atomic():
        post = record.select_for_update().filter(platform=Platform.YOUTUBE, status=Status.COMPLETE).first()
        video_id = youtube_video_id(post.url) if post else ""
        if not video_id:
            return fail("There is no completed YouTube upload to update.", status=409)
        options = cover_options(post.job, post.clip_index, post.platform)
        if not options["thumbnail_file"]:
            return fail("The JPG is unavailable. Save a thumbnail on the render first.")
        now = timezone.now()
        post.options.update(options)
        post.transfer = seal({"video_id": video_id, "cover_since": now.timestamp()})
        post.status = Status.PROCESSING
        post.next_at = now
        post.attempts = 0
        post.progress = 99
        post.message = "Thumbnail update queued for the existing YouTube video."
        post.save(update_fields=["options", "transfer", "status", "next_at", "attempts",
                                 "progress", "message", "updated_at"])
    return JsonResponse(publication_data(post))
