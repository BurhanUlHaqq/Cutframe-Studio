"""Workspace model preferences and write-only provider credentials."""

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django import forms
from django.conf import settings

from shorts_generator.config import LOCAL_WHISPER_DEVICE, LOCAL_WHISPER_MODEL
from shorts_generator.local.llm import (
    PROVIDERS, RankingConfigurationError, clean_key, environment_provider, normalize_endpoint, ranking_config,
)

from .models import WorkspaceAISettings


WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3", "turbo")
DEFAULTS = {"provider": "", "model": "", "base_url": "", "whisper_model": ""}


class AISettingsForm(forms.Form):
    require_model = True
    provider = forms.ChoiceField(required=False, choices=[("", "Server environment"), *(
        (provider, info[0]) for provider, info in PROVIDERS.items()
    )])
    model = forms.CharField(required=False, max_length=200)
    base_url = forms.CharField(required=False, max_length=500)
    api_key = forms.CharField(required=False, max_length=1000)
    remove_key = forms.BooleanField(required=False)
    whisper_model = forms.ChoiceField(required=False, choices=[("", "Server environment"), *(
        (model, model) for model in WHISPER_MODELS
    )])

    def clean(self):
        data = super().clean()
        provider = data.get("provider")
        if provider == "compatible":
            config = ranking_config(provider, data.get("model", ""), data.get("base_url", ""))
            try:
                data["base_url"] = normalize_endpoint(config.base_url, browser=True)
            except RankingConfigurationError as exc:
                self.add_error("base_url", str(exc))
            if self.require_model and not config.model:
                self.add_error("model", "Enter the model ID reported by your API endpoint.")
        elif provider in {"openai", "gemini", ""}:
            data["base_url"] = ""
        if not provider:
            data["api_key"] = ""
        elif data.get("api_key") and not clean_key(data["api_key"]):
            self.add_error("api_key", "Enter an API key issued by your provider.")
        return data


class ModelDiscoveryForm(AISettingsForm):
    require_model = False


def _cipher():
    digest = hmac.digest(settings.SECRET_KEY.encode(), b"cutframe-ranking-key-v1", hashlib.sha256)
    return Fernet(base64.urlsafe_b64encode(digest))


def saved_ai_options(owner):
    if owner:
        return WorkspaceAISettings.objects.filter(owner=owner).values_list("config", flat=True).first() or {}
    return {}


def merge_ai_options(data, saved):
    options = {field: data[field] for field in DEFAULTS}
    if data["api_key"] and not data["remove_key"]:
        options["secret"] = _cipher().encrypt(data["api_key"].encode()).decode()
    elif (data["provider"] and not data["remove_key"] and saved.get("secret")
          and all(saved.get(field) == options[field] for field in ("provider", "base_url"))):
        options["secret"] = saved["secret"]
    return options


def resolve_ai_options(options):
    key = ""
    if options.get("secret"):
        try:
            key = _cipher().decrypt(options["secret"].encode()).decode()
        except InvalidToken as exc:
            raise RankingConfigurationError("The stored credential could not be read. Enter the API key again in Settings.") from exc
    provider = options.get("provider", "")
    return ranking_config(provider, options.get("model", ""), options.get("base_url", ""), key)


def _public_connection(config):
    base_url = ""
    if config.provider == "compatible" and config.base_url:
        try:
            base_url = normalize_endpoint(config.base_url)
        except RankingConfigurationError:
            pass
    return {"provider": config.provider, "model": config.model, "base_url": base_url, "has_key": bool(config.api_key)}


def ai_settings_data(options):
    requested = DEFAULTS | {field: options[field] for field in DEFAULTS if field in options}
    try:
        environment = _public_connection(ranking_config())
    except RankingConfigurationError:
        environment = {"provider": environment_provider(), "model": "", "base_url": "", "has_key": False}
    try:
        connection = _public_connection(resolve_ai_options(options))
        connection["key_source"] = "saved" if options.get("secret") else "environment" if connection["has_key"] else ""
    except RankingConfigurationError:
        connection = {"provider": requested["provider"], "model": requested["model"], "key_source": ""}
    return {
        "settings": requested, "connection": connection, "environment": environment,
        "whisper": {"models": WHISPER_MODELS, "model": LOCAL_WHISPER_MODEL if LOCAL_WHISPER_MODEL in WHISPER_MODELS
                    else "Custom model", "device": LOCAL_WHISPER_DEVICE},
        "provider_defaults": {provider: ranking_config(provider).model for provider in PROVIDERS},
    }
