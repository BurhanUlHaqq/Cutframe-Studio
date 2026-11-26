"""Prepare local delivery records using the same checks for single posts and batches."""

import hashlib
import json
from contextlib import ExitStack
from datetime import timedelta

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from filelock import Timeout

from studio.exports import render_lock
from studio.models import Job

from .config import PublishError, client_config, publishing_settings
from .models import Connection, Platform, Publication, PublicationBatch
from .thumbnails import cover_options
from .worker import clip_path


class QueueError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def destination_account(connection, config, account_id):
    if connection and config.secret and connection.client_id == config.client_id:
        for account in connection.accounts:
            if account["id"] == account_id:
                return account
    raise QueueError("Connect the destination account before publishing.")


def prepare_post(job, fields, account, send_time):
    platform = fields["platform"]
    index = fields["clip_index"]
    try:
        path = clip_path(job, index)
        size = path.stat().st_size if platform == Platform.INSTAGRAM else None
    except (PublishError, OSError) as exc:
        raise QueueError("The rendered MP4 is unavailable. Render the clip again.") from exc
    clip = job.clips[index]
    start, end = clip.get("start_time"), clip.get("end_time")
    duration = clip.get("duration_seconds")
    if duration is None and isinstance(start, (int, float)) and isinstance(end, (int, float)):
        duration = end - start
    if platform == Platform.INSTAGRAM:
        if size > 300_000_000:
            raise QueueError("Instagram Reels support videos up to 300 MB.")
        if duration is not None and not 3 <= duration <= 900:
            raise QueueError("Instagram Reels must be between 3 seconds and 15 minutes.")
        if len(fields["caption"]) > 2200:
            raise QueueError("Instagram captions can have up to 2,200 characters.")
    if platform == Platform.TIKTOK and duration is not None and duration > 600:
        raise QueueError("TikTok inbox uploads support videos up to 10 minutes.")

    options = {name: fields[name] for name in ("title", "caption", "privacy", "made_for_kids", "synthetic")}
    if platform == Platform.YOUTUBE:
        chapters = clip.get("chapter_text")
        if chapters and not options["caption"].endswith(chapters):
            options["caption"] = "\n\n".join(filter(None, (options["caption"], chapters)))
        if len(options["caption"].encode("utf-8")) > 5000:
            raise QueueError("Keep the YouTube description and any chapter timestamps within 5,000 bytes.")
    options.update(cover_options(job, index, platform))
    return {"owner": job.owner, "job": job, "clip_index": index, "platform": platform,
            "account_id": account["id"], "account_label": account["label"][:180], "options": options,
            "scheduled_at": send_time, "next_at": send_time, "message": "Approved and queued for transfer."}


def queue_batch(owner, data):
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, cls=DjangoJSONEncoder).encode()).hexdigest()
    job_ids = sorted({row["job_id"] for row in data["clips"]})
    try:
        # Hold render locks through commit so cleanup can see the pending posts.
        with ExitStack() as locks, transaction.atomic():
            batch, created = PublicationBatch.objects.get_or_create(pk=data["request_id"], defaults={
                "owner": owner, "fingerprint": fingerprint,
            })
            if str(batch.owner) != str(owner) or batch.fingerprint != fingerprint:
                raise QueueError("This batch identifier has already been used for a different selection.", 409)
            if not created:
                return batch, list(batch.posts.order_by("scheduled_at", "created_at", "id")), False

            now = timezone.now()
            start = data["scheduled_at"] or now
            if data["scheduled_at"] and start <= now:
                raise QueueError("Choose a start time in the future.")
            jobs = Job.objects.filter(pk__in=job_ids, owner=owner, status=Job.Status.COMPLETE)
            if jobs.count() != len(job_ids):
                raise QueueError("One or more selected renders are unavailable.", 404)
            for job_id in job_ids:
                locks.enter_context(render_lock(job_id))
            by_id = jobs.in_bulk()
            if len(by_id) != len(job_ids):
                raise QueueError("One or more selected renders are unavailable.", 404)

            setup = publishing_settings()
            connections = {row.platform: row for row in Connection.objects.filter(owner=owner)}
            destinations = []
            for target in data["destinations"]:
                platform = target["platform"]
                try:
                    account = destination_account(connections.get(platform), client_config(platform, setup), target["account_id"])
                except QueueError as exc:
                    raise QueueError(f"{Platform(platform).label}. {exc}") from exc
                destinations.append((target, account))

            interval = timedelta(minutes=data["spacing_minutes"] or 0)
            pending = []
            for position, clip in enumerate(data["clips"]):
                job = by_id[clip["job_id"]]
                try:
                    send_time = start + interval * position
                except OverflowError as exc:
                    raise QueueError("Choose an earlier start time or a shorter schedule gap.") from exc
                for target, account in destinations:
                    try:
                        fields = prepare_post(job, data | clip | target, account, send_time)
                    except QueueError as exc:
                        raise QueueError(f"{clip['title']} · {Platform(target['platform']).label}. {exc}") from exc
                    pending.append(Publication(batch=batch, **fields))
            posts = Publication.objects.bulk_create(pending)
            return batch, posts, True
    except Timeout as exc:
        raise QueueError("A selected render is in use. Try submitting the batch again in a moment.", 409) from exc
