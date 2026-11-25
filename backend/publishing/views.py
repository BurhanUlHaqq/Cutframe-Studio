import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.db import transaction
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from studio.models import Job
from studio.views import ensure_owner, form_data

from .config import PublishError, client_config, publishing_settings, seal
from .forms import FinishForm, InstagramAppForm, NetworkSettingsForm, PlatformAppForm, PublishForm
from .models import Authorization, Connection, Platform, Publication, PublishingSettings
from .oauth import authorize_url, exchange, state_digest
from .worker import clip_path


Status = Publication.Status


def fail(message, status=400):
    return JsonResponse({"error": message}, status=status)


def publication_data(post):
    return {
        "id": post.id, "job_id": post.job_id, "clip_index": post.clip_index, "platform": post.platform,
        "account": post.account_label, "title": post.options["title"], "caption": post.options["caption"],
        "status": post.status, "progress": post.progress,
        "scheduled_at": post.scheduled_at, "started_at": post.started_at, "message": post.message, "url": post.url,
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
        return JsonResponse({"posts": [publication_data(post) for post in Publication.objects.filter(owner=owner)[:100]]})
    data, error = form_data(request, PublishForm)
    if error is not None:
        return error
    if not owner:
        raise Http404
    job = get_object_or_404(Job, pk=data["job_id"], owner=owner, status=Job.Status.COMPLETE)
    platform = data["platform"]
    connection = Connection.objects.filter(owner=owner, platform=platform).first()
    account = next((account for account in connection.accounts if account["id"] == data["account_id"]), None) if connection else None
    config = client_config(platform)
    if not account or config.client_id != connection.client_id or not config.secret:
        return fail("Connect the destination account before publishing.")
    try:
        path = clip_path(job, data["clip_index"])
    except PublishError as exc:
        return fail(str(exc))
    clip = job.clips[data["clip_index"]]
    start, end = clip.get("start_time"), clip.get("end_time")
    duration = end - start if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None
    if platform == Platform.INSTAGRAM:
        if path.stat().st_size > 300_000_000:
            return fail("Instagram Reels support videos up to 300 MB.")
        if duration is not None and not 3 <= duration <= 900:
            return fail("Instagram Reels must be between 3 seconds and 15 minutes.")
    if platform == Platform.TIKTOK and duration is not None and duration > 600:
        return fail("TikTok inbox uploads support videos up to 10 minutes.")
    chapters = clip.get("chapter_text")
    if platform == Platform.YOUTUBE and chapters and not data["caption"].endswith(chapters):
        data["caption"] = "\n\n".join(filter(None, (data["caption"], chapters)))
        if len(data["caption"].encode("utf-8")) > 5000:
            return fail("Shorten the YouTube description to fit the included chapters within 5,000 bytes.")
    now = data["scheduled_at"] or timezone.now()
    post, created = Publication.objects.get_or_create(pk=data["request_id"], defaults={
        "owner": job.owner, "job": job, "clip_index": data["clip_index"], "platform": platform,
        "account_id": account["id"], "account_label": account["label"][:180],
        "options": {field: data[field] for field in ("title", "caption", "privacy", "made_for_kids", "synthetic")},
        "scheduled_at": now, "next_at": now, "message": "Approved and queued for transfer.",
    })
    if post.owner != job.owner:
        return fail("This request identifier is unavailable.", status=409)
    return JsonResponse(publication_data(post), status=201 if created else 200)


@require_POST
@never_cache
def post_action(request, post_id, action):
    owner = request.session.get("studio_owner")
    record = Publication.objects.filter(owner=owner, pk=post_id)
    if not record.exists():
        raise Http404
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
