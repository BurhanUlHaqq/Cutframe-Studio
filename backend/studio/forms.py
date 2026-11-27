import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from django import forms

from shorts_generator.local.cover_design import ACCENTS, clean_headline
from shorts_generator.timestamps import parse_source_timestamp
from shorts_generator.url_validation import validate_youtube_url

from .models import (
    ASPECT_RATIOS, CAPTION_STYLES, DEFAULT_VIRAL_SCORE, EDITING_STYLES, FRAMING_MODES, SOURCE_QUALITIES,
    ClipSelection, StudioMode, HighlightMethod, WorkspacePreferences,
)
from .uploads import VIDEO_EXTENSIONS, upload_limit


class RenderOptionsForm(forms.Form):
    studio_mode = forms.ChoiceField(choices=StudioMode.choices)
    highlight_minutes = forms.IntegerField(min_value=1, max_value=30)
    highlight_method = forms.ChoiceField(choices=HighlightMethod.choices)
    editing_style = forms.ChoiceField(choices=EDITING_STYLES)
    selection_mode = forms.ChoiceField(choices=ClipSelection.choices)
    clip_count = forms.IntegerField(min_value=1, max_value=10)
    min_viral_score = forms.IntegerField(required=False, min_value=0, max_value=100)
    aspect_ratio = forms.ChoiceField(choices=ASPECT_RATIOS)
    quality = forms.ChoiceField(choices=SOURCE_QUALITIES)
    language = forms.RegexField(r"^[a-z]{2}$", max_length=2, required=False)
    framing = forms.ChoiceField(choices=FRAMING_MODES)
    caption_style = forms.ChoiceField(choices=CAPTION_STYLES)
    min_clip_seconds = forms.IntegerField(required=False, min_value=1, max_value=180)
    max_clip_seconds = forms.IntegerField(required=False, min_value=1, max_value=180)

    def clean(self):
        data = super().clean()
        if "min_viral_score" in data and data["min_viral_score"] is None:
            if data.get("selection_mode") == ClipSelection.SCORE:
                self.add_error("min_viral_score", "Enter a minimum viral score.")
            else:
                data["min_viral_score"] = DEFAULT_VIRAL_SCORE
        minimum, maximum = data.get("min_clip_seconds"), data.get("max_clip_seconds")
        if minimum is not None and maximum is not None and minimum > maximum:
            self.add_error("max_clip_seconds", "The maximum clip length must be at least the minimum.")
        return data


class PreferencesForm(RenderOptionsForm):
    theme = forms.ChoiceField(choices=WorkspacePreferences.Theme.choices)
    show_scores = forms.TypedChoiceField(
        choices=[("True", "Show"), ("False", "Hide")], coerce=lambda value: value == "True",
    )


class SourceTimestampField(forms.CharField):
    def __init__(self):
        super().__init__(max_length=24, required=False)

    def clean(self, value):
        value = super().clean(value)
        try:
            return parse_source_timestamp(value)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc


class RenderJobForm(RenderOptionsForm):
    source_start = SourceTimestampField()
    source_end = SourceTimestampField()

    def clean(self):
        data = super().clean()
        start, end = data.pop("source_start", None), data.pop("source_end", None)
        if "source_start" not in self.errors and "source_end" not in self.errors:
            if start is None and end is not None:
                self.add_error("source_start", "Enter a start time for this source range.")
            elif end is None and start is not None:
                self.add_error("source_end", "Enter an end time for this source range.")
            elif start is not None and end <= start:
                self.add_error("source_end", "The end time must be after the start time.")
        data.update(source_start_ms=start, source_end_ms=end)
        if data.get("studio_mode") == StudioMode.HIGHLIGHTS:
            data.update(aspect_ratio="16:9", editing_style="balanced")
        if data.get("studio_mode") == StudioMode.HIGHLIGHTS or (start is not None and end is not None):
            data.update(clip_count=1, selection_mode=ClipSelection.COUNT, min_clip_seconds=None, max_clip_seconds=None)
        return data


class JobForm(RenderJobForm):
    source_url = forms.CharField(max_length=2048)

    def clean_source_url(self):
        try:
            url = urlsplit(validate_youtube_url(self.cleaned_data["source_url"]))
        except ValueError as exc:
            raise forms.ValidationError("Enter an HTTPS link to one YouTube video.") from exc

        parts = url.path.strip("/").split("/")
        if url.hostname in {"youtu.be", "www.youtu.be"} and len(parts) == 1:
            video_id = parts[0]
        elif parts == ["watch"]:
            video_id = parse_qs(url.query).get("v", [""])[0]
        elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
            video_id = parts[1]
        else:
            video_id = ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise forms.ValidationError("Enter a link to one YouTube video.")
        return f"https://www.youtube.com/watch?v={video_id}"


class ClipTextForm(forms.Form):
    title = forms.CharField(max_length=100)
    description = forms.CharField(max_length=1200)

    def clean_title(self):
        return " ".join(self.cleaned_data["title"].split())


class ThumbnailDesignForm(forms.Form):
    frame = forms.IntegerField(min_value=0, max_value=2)
    headline = forms.CharField(required=False, max_length=52)
    accent = forms.ChoiceField(choices=[(name, name) for name in ACCENTS])
    placement = forms.ChoiceField(choices=[(name, name) for name in ("auto", "top", "bottom")])

    def clean_headline(self):
        return clean_headline(self.cleaned_data["headline"])


class UploadJobForm(RenderJobForm):
    video = forms.FileField(max_length=255)

    def __init__(self, *args, max_upload_bytes=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_upload_bytes = max_upload_bytes

    def clean_video(self):
        video = self.cleaned_data["video"]
        if Path(video.name).suffix.lower() not in VIDEO_EXTENSIONS:
            raise forms.ValidationError("Choose a supported video file, such as MP4, MOV, MKV, or WebM.")
        if video.size > (self.max_upload_bytes or upload_limit()):
            raise forms.ValidationError("This video exceeds the installation upload limit.")
        return video


class AlternateRenderForm(RenderJobForm):
    pass


class StorageSettingsForm(forms.Form):
    max_upload_mib = forms.IntegerField(min_value=1, max_value=16384)
