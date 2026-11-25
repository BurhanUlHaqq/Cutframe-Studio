from uuid import uuid4

from django.db import models


class Platform(models.TextChoices):
    YOUTUBE = "youtube", "YouTube"
    TIKTOK = "tiktok", "TikTok"
    INSTAGRAM = "instagram", "Instagram Reels"


class PublishingSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    studio_origin = models.CharField(max_length=255, default="http://localhost:5031")
    callback_origin = models.CharField(max_length=255, blank=True, default="")
    instagram_login_config = models.CharField(max_length=64, blank=True, default="")
    meta_api_version = models.CharField(max_length=12, default="v27.0")
    clients = models.JSONField(default=dict)


class Connection(models.Model):
    owner = models.UUIDField()
    platform = models.CharField(max_length=12, choices=Platform)
    client_id = models.CharField(max_length=255)
    accounts = models.JSONField(default=list)
    credentials = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "platform"], name="publish_workspace_platform")]


class Authorization(models.Model):
    state = models.CharField(max_length=64, primary_key=True)
    owner = models.UUIDField()
    platform = models.CharField(max_length=12, choices=Platform)
    client_id = models.CharField(max_length=255)
    redirect_uri = models.URLField(max_length=512)
    verifier = models.CharField(max_length=128, blank=True)
    expires_at = models.DateTimeField(db_index=True)


class Publication(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        PROCESSING = "processing", "Processing"
        INBOX = "inbox", "Ready in TikTok"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"
        REVIEW = "review", "Check platform"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    owner = models.UUIDField(db_index=True)
    ACTIVE = (Status.QUEUED, Status.SENDING, Status.PROCESSING)

    job = models.ForeignKey("studio.Job", on_delete=models.SET_NULL, null=True)
    clip_index = models.PositiveSmallIntegerField()
    platform = models.CharField(max_length=12, choices=Platform)
    account_id = models.CharField(max_length=180)
    account_label = models.CharField(max_length=180)
    options = models.JSONField()
    status = models.CharField(max_length=12, choices=Status, default=Status.QUEUED)
    scheduled_at = models.DateTimeField()
    next_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    progress = models.PositiveSmallIntegerField(default=0)
    message = models.CharField(max_length=300, blank=True)
    transfer = models.TextField(blank=True)
    url = models.URLField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "next_at"], name="publish_queue_idx")]
