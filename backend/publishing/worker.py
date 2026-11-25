import logging
import time
from datetime import timedelta

import httpx
from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone
from filelock import FileLock

from studio.exports import job_directory

from . import adapters
from .config import PublishError, unseal
from .models import Connection, Platform, Publication
from .oauth import access_token


logger = logging.getLogger(__name__)
Status = Publication.Status
SENDERS = {Platform.YOUTUBE: adapters.youtube, Platform.TIKTOK: adapters.tiktok, Platform.INSTAGRAM: adapters.instagram}


def clip_path(job, index):
    root = (job_directory(job.id) / "files").resolve()
    try:
        path = (root / f"{job.clips[index]['name']}.mp4").resolve()
    except (IndexError, KeyError) as exc:
        raise PublishError("This clip is no longer available.") from exc
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size == 0:
        raise PublishError("The rendered MP4 is unavailable. Render the clip again.")
    return path


def process_next():
    now = timezone.now()
    queue = Publication.objects.filter(status__in=[Status.QUEUED, Status.PROCESSING], next_at__lte=now)
    post = queue.select_related("job").order_by("next_at", "created_at").first()
    if not post or not queue.filter(pk=post.pk).update(
        status=Status.SENDING, started_at=post.started_at or now, updated_at=now,
        message="Sending video or checking platform status.",
    ):
        return False
    post.started_at = post.started_at or now
    try:
        connection = Connection.objects.filter(owner=post.owner, platform=post.platform).first()
        if not connection or not any(account["id"] == post.account_id for account in connection.accounts):
            raise PublishError("Reconnect the original destination account before retrying.")
        if post.job.owner != post.owner:
            raise PublishError("The rendered clip is no longer available.")
        path = clip_path(post.job, post.clip_index)
        with httpx.Client(timeout=httpx.Timeout(90, connect=10, write=120), follow_redirects=False) as client, path.open("rb") as file:
            token = access_token(client, connection, post.account_id)
            result = SENDERS[post.platform](client, post, token, file, path.stat().st_size, unseal(post.transfer))
        post.status, post.message, post.url = result.status, result.message, result.url
        post.next_at = timezone.now() + timedelta(seconds=result.delay)
        post.attempts = 0
        if post.status in {Status.COMPLETE, Status.INBOX}:
            post.progress = 100
        if post.status == Status.COMPLETE:
            post.transfer = ""
    except PublishError as exc:
        record_failure(post, exc)
    except Exception as exc:
        logger.error("Publication %s failed (%s)", post.id, type(exc).__name__)
        record_failure(post, PublishError("The publishing worker could not complete this step. Check the platform before trying again.",
                                          review=bool(post.transfer)))
    post.save(update_fields=["status", "message", "url", "next_at", "attempts", "progress", "transfer", "updated_at"])
    return True


def record_failure(post, failure):
    post.message = str(failure)
    post.attempts += 1
    if failure.retry and post.attempts < 4:
        post.status = Status.PROCESSING
        post.next_at = timezone.now() + timedelta(seconds=30 * post.attempts)
        return
    try:
        publish_requested = post.platform == Platform.INSTAGRAM and unseal(post.transfer).get("step") == "publishing"
    except PublishError:
        publish_requested = True
    post.status = Status.REVIEW if failure.review or publish_requested else Status.FAILED
    if post.status == Status.REVIEW:
        post.message = "Check the destination platform before starting another upload. The final status could not be confirmed."


def run(once=False):
    with FileLock(settings.STUDIO_DATA_DIR / "publisher.lock", timeout=0):
        Publication.objects.filter(status=Status.SENDING).update(status=Status.PROCESSING, next_at=timezone.now())
        while True:
            close_old_connections()
            handled = process_next()
            if once:
                return
            if not handled:
                time.sleep(3)
