from django import forms
from django.utils import timezone

from .config import origin
from .models import Platform


class NetworkSettingsForm(forms.Form):
    studio_origin = forms.CharField(max_length=255)
    callback_origin = forms.CharField(max_length=255, required=False)

    def clean_studio_origin(self):
        normalized = origin(self.cleaned_data["studio_origin"])
        if not normalized:
            raise forms.ValidationError("Enter the local Cutframe address, such as http://localhost:5031.")
        return normalized

    def clean_callback_origin(self):
        value = self.cleaned_data["callback_origin"]
        normalized = origin(value, public=True)
        if value and not normalized:
            raise forms.ValidationError("Enter the public HTTPS callback origin without a path.")
        return normalized


class PlatformAppForm(forms.Form):
    client_id = forms.RegexField(r"^[a-zA-Z0-9._-]+$", required=False, max_length=255)
    client_secret = forms.CharField(required=False, max_length=1000)
    remove_secret = forms.BooleanField(required=False)

    def clean(self):
        data = super().clean()
        if data.get("client_secret") and not data.get("client_id"):
            self.add_error("client_id", "Enter the developer app ID or client key.")
        return data


class InstagramAppForm(PlatformAppForm):
    instagram_login_config = forms.RegexField(r"^[0-9]+$", required=False, max_length=64)
    meta_api_version = forms.RegexField(r"^v[0-9]+\.[0-9]+$", max_length=12)


MAX_BATCH_CLIPS = 50
MAX_BATCH_POSTS = 150


class PostContentForm(forms.Form):
    job_id = forms.UUIDField()
    clip_index = forms.IntegerField(min_value=0)
    title = forms.CharField(max_length=100)
    caption = forms.CharField(max_length=5000, required=False)

    def clean_title(self):
        title = self.cleaned_data["title"]
        if any(char in title for char in "<>\r\n"):
            raise forms.ValidationError("Enter a title on one line without angle brackets.")
        return title


class DestinationForm(forms.Form):
    platform = forms.ChoiceField(choices=Platform.choices)
    account_id = forms.CharField(max_length=180)


class PublishOptionsForm(forms.Form):
    privacy = forms.ChoiceField(choices=[("private", "Private"), ("unlisted", "Unlisted"), ("public", "Public")],
                                required=False)
    made_for_kids = forms.TypedChoiceField(choices=[("true", "Yes"), ("false", "No")],
                                          coerce=lambda value: value == "true", required=False)
    synthetic = forms.BooleanField(required=False)
    scheduled_at = forms.DateTimeField(required=False)
    confirm = forms.BooleanField()
    highlight_reviewed = forms.BooleanField(required=False)


class PublishForm(PostContentForm, DestinationForm, PublishOptionsForm):
    request_id = forms.UUIDField()

    def clean(self):
        data = super().clean()
        when = data.get("scheduled_at")
        if when and when <= timezone.now():
            self.add_error("scheduled_at", "Choose a time in the future.")
        if data.get("platform") == Platform.YOUTUBE:
            if not data.get("privacy"):
                self.add_error("privacy", "Choose the visibility for this YouTube video.")
            if data.get("made_for_kids") == "":
                self.add_error("made_for_kids", "Choose whether this video is made for kids.")
            if len(data.get("caption", "").encode()) > 5000:
                self.add_error("caption", "The YouTube description must fit within 5,000 bytes.")
        if data.get("platform") == Platform.INSTAGRAM and len(data.get("caption", "")) > 2200:
            self.add_error("caption", "Instagram captions can have up to 2,200 characters.")
        return data


class BulkPublishForm(PublishOptionsForm):
    request_id = forms.UUIDField()
    clips = forms.JSONField()
    destinations = forms.JSONField()
    spacing_minutes = forms.IntegerField(min_value=0, max_value=10080, required=False)

    def clean_clips(self):
        return self._rows("clips", PostContentForm, ("job_id", "clip_index"), "Clip", MAX_BATCH_CLIPS)

    def clean_destinations(self):
        return self._rows("destinations", DestinationForm, ("platform", "account_id"), "Destination", MAX_BATCH_POSTS)

    def _rows(self, field, form_class, identifier, label, limit):
        rows = self.cleaned_data[field]
        if not isinstance(rows, list) or not 1 <= len(rows) <= limit:
            raise forms.ValidationError(f"Choose between 1 and {limit} {field}.")
        cleaned, seen = [], set()
        for index, fields in enumerate(rows, 1):
            if not isinstance(fields, dict):
                raise forms.ValidationError(f"{label} {index} is invalid.")
            form = form_class(fields)
            if not form.is_valid():
                message = next(iter(form.errors.values()))[0]
                raise forms.ValidationError(f"{label} {index}. {message}")
            key = tuple(form.cleaned_data[name] for name in identifier)
            if key in seen:
                raise forms.ValidationError(f"{label} {index} is selected more than once.")
            seen.add(key)
            cleaned.append(form.cleaned_data)
        return cleaned

    def clean(self):
        data = super().clean()
        clips, destinations = data.get("clips", []), data.get("destinations", [])
        if len(clips) * len(destinations) > MAX_BATCH_POSTS:
            self.add_error(None, f"Choose up to {MAX_BATCH_POSTS} total posts per batch, counting each destination separately.")
        if any(row["platform"] == Platform.YOUTUBE for row in destinations):
            if not data.get("privacy"):
                self.add_error("privacy", "Choose the visibility for YouTube videos.")
            if data.get("made_for_kids") == "":
                self.add_error("made_for_kids", "Choose whether the YouTube videos are made for kids.")
        if any(row["platform"] == Platform.TIKTOK for row in destinations) and len(clips) > 5:
            if (data.get("spacing_minutes") or 0) * 5 < 1440:
                self.add_error("spacing_minutes", "Schedule at most five TikTok inbox drafts in any 24-hour window in this batch.")
        return data


class FinishForm(forms.Form):
    platform = forms.ChoiceField(choices=Platform.choices)
    code = forms.CharField(max_length=2048)
    state = forms.CharField(max_length=128)
