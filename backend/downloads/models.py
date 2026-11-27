import uuid

from django.db import models


class Download(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Downloading"
        CANCELLING = "cancelling", "Stopping"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETE = "complete", "Ready"
        PARTIAL = "partial", "Partially ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.UUIDField(db_index=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveIntegerField(null=True, blank=True)
    is_playlist = models.BooleanField(default=False)
    playlist_start = models.PositiveIntegerField(default=1)
    playlist_end = models.PositiveIntegerField(null=True, blank=True)
    playlist_limited = models.BooleanField(default=False)
    source_start_ms = models.PositiveBigIntegerField(null=True, blank=True)
    source_end_ms = models.PositiveBigIntegerField(null=True, blank=True)
    output = models.CharField(max_length=5, default="video")
    audio_bitrate = models.PositiveSmallIntegerField(default=192)
    source_url = models.URLField(max_length=600)
    platform = models.CharField(max_length=20)
    quality = models.CharField(max_length=4)
    status = models.CharField(max_length=12, choices=Status, default=Status.QUEUED, db_index=True)
    activity = models.CharField(max_length=220, default="Waiting for the downloader.")
    title = models.CharField(max_length=240, blank=True)
    extension = models.CharField(max_length=8, blank=True)
    bytes_received = models.PositiveBigIntegerField(default=0)
    bytes_expected = models.PositiveBigIntegerField(default=0)
    duration = models.FloatField(null=True, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
