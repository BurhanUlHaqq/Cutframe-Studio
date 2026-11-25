"""Explicitly import application values from an older Cutframe configuration."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from dotenv import dotenv_values

from publishing.config import origin, seal
from publishing.forms import InstagramAppForm
from publishing.models import Platform, PublishingSettings
from shorts_generator.local.llm import RankingConfigurationError, clean_key, normalize_endpoint
from studio.ai_settings import AISettingsForm, seal_api_key
from studio.models import InstallationSettings


class Command(BaseCommand):
    help = "Copy legacy app settings into the database without printing their values."

    def add_arguments(self, parser):
        parser.add_argument("file", type=Path)

    @transaction.atomic
    def handle(self, *args, **options):
        source = options["file"]
        if not source.is_file():
            raise CommandError("The legacy settings file could not be found.")
        try:
            raw = dotenv_values(source, interpolate=False, encoding="utf-8-sig")
        except (OSError, UnicodeError):
            raise CommandError("The legacy settings file could not be read.") from None
        values = {name: value.strip() for name, value in raw.items() if value and value.strip()}
        installation, _ = InstallationSettings.objects.select_for_update().get_or_create(pk=1)
        profiles = installation.ai.setdefault("profiles", {})
        for provider, prefix in (("openai", "OPENAI"), ("gemini", "GEMINI"), ("compatible", "LLM")):
            model, key = values.get(f"{prefix}_MODEL", ""), clean_key(values.get(f"{prefix}_API_KEY", ""))
            base_url = ""
            if provider == "compatible" and values.get("LLM_BASE_URL"):
                try:
                    base_url = normalize_endpoint(values["LLM_BASE_URL"])
                except RankingConfigurationError:
                    raise CommandError("The legacy compatible API base URL is invalid.") from None
            if provider not in profiles and (model or key or base_url):
                profiles[provider] = {"model": model, "base_url": base_url}
                if key:
                    profiles[provider]["secret"] = seal_api_key(key)
        provider = values.get("LLM_PROVIDER", "compatible" if values.get("LLM_BASE_URL") else "openai").lower()
        if provider not in {"openai", "gemini", "compatible"}:
            raise CommandError("The legacy ranking provider is invalid.")
        installation.ai.setdefault("provider", provider)
        whisper = {}
        for name in ("whisper_model", "whisper_device", "vad_parameters"):
            key = "LOCAL_WHISPER_" + (name.upper().removeprefix("WHISPER_"))
            if key in values:
                whisper[name] = values[key]
        if "LOCAL_WHISPER_VAD_FILTER" in values:
            whisper["whisper_vad"] = "on" if values["LOCAL_WHISPER_VAD_FILTER"].lower() == "true" else "off"
        form = AISettingsForm(whisper)
        if not form.is_valid():
            raise CommandError("One or more legacy Whisper configuration values are invalid.")
        for name in whisper:
            installation.ai.setdefault(name, form.cleaned_data[name])
        if installation.max_upload_bytes is None and "STUDIO_MAX_UPLOAD_BYTES" in values:
            try:
                maximum = int(values["STUDIO_MAX_UPLOAD_BYTES"])
                if not 0 < maximum <= 2**63 - 1:
                    raise ValueError
            except ValueError:
                raise CommandError("The legacy upload limit is invalid.") from None
            installation.max_upload_bytes = maximum
        installation.save(update_fields=["ai", "max_upload_bytes"])

        publishing, created = PublishingSettings.objects.select_for_update().get_or_create(pk=1)
        if created and values.get("PUBLISH_STUDIO_ORIGIN"):
            local = origin(values["PUBLISH_STUDIO_ORIGIN"])
            if not local:
                raise CommandError("The legacy studio address must use a local loopback host.")
            publishing.studio_origin = local
        if not publishing.callback_origin and values.get("PUBLISH_CALLBACK_ORIGIN"):
            callback = origin(values["PUBLISH_CALLBACK_ORIGIN"], public=True)
            if not callback:
                raise CommandError("The legacy callback address must be a public HTTPS origin.")
            publishing.callback_origin = callback
        for platform in Platform.values:
            prefix = f"PUBLISH_{platform.upper()}_CLIENT"
            client_id = values.get(f"{prefix}_ID", "")
            if platform not in publishing.clients and client_id:
                secret = values.get(f"{prefix}_SECRET", "")
                publishing.clients[platform] = {"id": client_id, "secret": seal({"value": secret}) if secret else ""}
        if created:
            form = InstagramAppForm({
                "meta_api_version": values.get("PUBLISH_META_API_VERSION", publishing.meta_api_version),
                "instagram_login_config": values.get("PUBLISH_INSTAGRAM_LOGIN_CONFIG_ID", ""),
            })
            if not form.is_valid():
                raise CommandError("The legacy Meta API version or login configuration ID is invalid.")
            publishing.meta_api_version = form.cleaned_data["meta_api_version"]
            publishing.instagram_login_config = form.cleaned_data["instagram_login_config"]
        publishing.save()
        self.stdout.write(self.style.SUCCESS("Legacy app settings imported. Existing saved values were kept."))
