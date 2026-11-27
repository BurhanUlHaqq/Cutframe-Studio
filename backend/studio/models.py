import uuid

from django.db import models

from shorts_generator.edit_styles import STYLES


ASPECT_RATIOS = [("9:16", "Vertical"), ("1:1", "Square"), ("16:9", "Landscape")]
SOURCE_QUALITIES = [("480", "480p"), ("720", "720p"), ("1080", "1080p")]
FRAMING_MODES = [
    ("auto", "Smart framing"), ("stream", "Screen + streamer"),
    ("center", "Center crop"), ("fit", "Fit with blur"),
]
CAPTION_STYLES = [("classic", "Classic captions"), ("highlight", "Highlighted words"), ("off", "Captions off")]
EDITING_STYLES = [(key, style.label) for key, style in STYLES.items()]
DEFAULT_VIRAL_SCORE = 80
DEFAULT_HIGHLIGHT_MINUTES = 10
RENDER_OPTIONS = (
    "studio_mode", "highlight_minutes", "highlight_method", "editing_style",
    "selection_mode", "clip_count", "min_viral_score", "aspect_ratio", "quality", "language", "framing", "caption_style",
    "min_clip_seconds", "max_clip_seconds",
)
PREFERENCE_FIELDS = (*RENDER_OPTIONS, "theme", "show_scores")


class ClipSelection(models.TextChoices):
    COUNT = "count", "By clip count"
    SCORE = "score", "By viral score"


class StudioMode(models.TextChoices):
    SHORTS = "shorts", "Short Clips"
    HIGHLIGHTS = "highlights", "Highlights"

    @classmethod
    def alternate(cls, mode):
        return cls.SHORTS if mode == cls.HIGHLIGHTS else cls.HIGHLIGHTS


class HighlightMethod(models.TextChoices):
    TRANSCRIPT = "transcript", "AI transcript"
    ACTIVITY = "activity", "Local visual activity"


class WorkspacePreferences(models.Model):
    class Theme(models.TextChoices):
        LIGHT = "light", "Light"
        DARK = "dark", "Dark"
        SYSTEM = "system", "Match system"

    owner = models.UUIDField(primary_key=True, editable=False)
    studio_mode = models.CharField(max_length=10, choices=StudioMode, default=StudioMode.SHORTS)
    highlight_minutes = models.PositiveSmallIntegerField(default=DEFAULT_HIGHLIGHT_MINUTES)
    highlight_method = models.CharField(max_length=10, choices=HighlightMethod, default=HighlightMethod.TRANSCRIPT)
    editing_style = models.CharField(max_length=12, choices=EDITING_STYLES, default="balanced")
    selection_mode = models.CharField(max_length=5, choices=ClipSelection, default=ClipSelection.COUNT)
    clip_count = models.PositiveSmallIntegerField(default=5)
    min_viral_score = models.PositiveSmallIntegerField(default=DEFAULT_VIRAL_SCORE)
    aspect_ratio = models.CharField(max_length=5, choices=ASPECT_RATIOS, default="9:16")
    quality = models.CharField(max_length=4, choices=SOURCE_QUALITIES, default="720")
    language = models.CharField(max_length=2, blank=True, default="")
    framing = models.CharField(max_length=8, choices=FRAMING_MODES, default="auto")
    caption_style = models.CharField(max_length=9, choices=CAPTION_STYLES, default="classic")
    min_clip_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    max_clip_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    theme = models.CharField(max_length=6, choices=Theme, default=Theme.LIGHT)
    show_scores = models.BooleanField(default=True)


class WorkspaceAISettings(models.Model):
    owner = models.UUIDField(primary_key=True, editable=False)
    config = models.JSONField(default=dict)
    description_model = models.CharField(max_length=200, blank=True, default="")
    description_automatic = models.BooleanField(default=False)
    description_connection = models.JSONField(default=dict, blank=True)


class WorkspaceThumbnailSettings(models.Model):
    owner = models.UUIDField(primary_key=True, editable=False)
    config = models.JSONField(default=dict)


class InstallationSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    ai = models.JSONField(default=dict)
    max_upload_bytes = models.PositiveBigIntegerField(null=True, blank=True)


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Rendering"
        CANCELLING = "cancelling", "Stopping"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETE = "complete", "Ready"
        FAILED = "failed", "Failed"
        DELETING = "deleting", "Cleanup incomplete"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.UUIDField(db_index=True)
    source_url = models.URLField(blank=True)
    video_id = models.CharField(max_length=11, blank=True)
    source_name = models.CharField(max_length=255, blank=True)
    source_render = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="alternate_renders")
    studio_mode = models.CharField(max_length=10, choices=StudioMode, default=StudioMode.SHORTS)
    highlight_minutes = models.PositiveSmallIntegerField(default=DEFAULT_HIGHLIGHT_MINUTES)
    highlight_method = models.CharField(max_length=10, choices=HighlightMethod, default=HighlightMethod.TRANSCRIPT)
    editing_style = models.CharField(max_length=12, choices=EDITING_STYLES, default="balanced")
    selection_mode = models.CharField(max_length=5, choices=ClipSelection, default=ClipSelection.COUNT)
    clip_count = models.PositiveSmallIntegerField()
    min_viral_score = models.PositiveSmallIntegerField(default=DEFAULT_VIRAL_SCORE)
    aspect_ratio = models.CharField(max_length=5)
    quality = models.CharField(max_length=4)
    language = models.CharField(max_length=2, blank=True)
    framing = models.CharField(max_length=8, choices=FRAMING_MODES, default="auto")
    caption_style = models.CharField(max_length=9, choices=CAPTION_STYLES, default="off")
    min_clip_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    max_clip_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.QUEUED, db_index=True)
    stage = models.CharField(max_length=12, default="queue")
    progress = models.PositiveSmallIntegerField(default=0)
    activity = models.CharField(max_length=240, blank=True)
    skip_ai_covers = models.BooleanField(default=False)
    events = models.JSONField(default=list, blank=True)
    clips = models.JSONField(default=list)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    ACTIVE = (Status.QUEUED, Status.RUNNING, Status.CANCELLING)

    class Meta:
        ordering = ["-created_at", "id"]
