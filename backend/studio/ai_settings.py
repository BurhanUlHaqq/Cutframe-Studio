"""Database-backed model settings and write-only provider credentials."""

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django import forms
from django.conf import settings

from shorts_generator.config import DEFAULT_WHISPER_MODEL
from shorts_generator.local.llm import PROVIDERS, RankingConfigurationError, clean_key, normalize_endpoint, ranking_config

from .models import InstallationSettings, WorkspaceAISettings


WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3", "turbo")
WHISPER_FIELDS = ("whisper_model", "whisper_device", "whisper_vad", "vad_parameters")
DEFAULTS = {field: "" for field in ("provider", "model", "base_url", *WHISPER_FIELDS)}
DEFAULTS["vad_parameters"] = None
VAD_KEYS = {
    "threshold", "neg_threshold", "min_speech_duration_ms", "max_speech_duration_s",
    "min_silence_duration_ms", "speech_pad_ms",
}


class AISettingsForm(forms.Form):
    require_model = True
    provider = forms.ChoiceField(required=False, choices=[("", "Installation defaults"), *(
        (provider, info[0]) for provider, info in PROVIDERS.items()
    )])
    model = forms.CharField(required=False, max_length=200)
    base_url = forms.CharField(required=False, max_length=500)
    api_key = forms.CharField(required=False, max_length=1000)
    remove_key = forms.BooleanField(required=False)
    whisper_model = forms.CharField(required=False, max_length=120)
    whisper_device = forms.ChoiceField(required=False, choices=[(value, value) for value in ("", "auto", "cpu", "cuda")])
    whisper_vad = forms.ChoiceField(required=False, choices=[(value, value) for value in ("", "off", "on")])
    vad_parameters = forms.JSONField(required=False)

    def __init__(self, *args, installation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.installation = installation or {}

    def clean_whisper_model(self):
        value = self.cleaned_data["whisper_model"]
        if value and (value.startswith((".", "/")) or "\\" in value or ".." in value or value.count("/") > 1):
            raise forms.ValidationError("Enter a Whisper model name or a Hugging Face repository ID.")
        return value

    def clean_vad_parameters(self):
        value = self.cleaned_data["vad_parameters"]
        if value is None:
            return None
        if not isinstance(value, dict) or value.keys() - VAD_KEYS or any(
            type(number) not in {int, float} or not 0 <= number <= 86_400_000
            for number in value.values()
        ):
            raise forms.ValidationError("Enter valid numeric Whisper VAD parameters as a JSON object.")
        if any(value.get(name, 0) > 1 for name in ("threshold", "neg_threshold")):
            raise forms.ValidationError("Speech thresholds must be between 0 and 1.")
        return value

    def clean(self):
        data = super().clean()
        provider = data.get("provider")
        if provider == "compatible":
            saved = self.installation.get("profiles", {}).get(provider, {})
            try:
                data["base_url"] = normalize_endpoint(data.get("base_url") or saved.get("base_url", ""), browser=True,
                                                      trusted_url=saved.get("base_url", ""))
                if self.require_model and not (data.get("model") or
                                               data["base_url"] == saved.get("base_url") and saved.get("model")):
                    self.add_error("model", "Enter the model ID reported by your API endpoint.")
            except RankingConfigurationError as exc:
                self.add_error("base_url", str(exc))
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


def seal_api_key(key):
    return _cipher().encrypt(key.encode()).decode()


def installation_ai():
    return InstallationSettings.objects.filter(pk=1).values_list("ai", flat=True).first() or {}


def saved_ai_options(owner):
    if owner:
        return WorkspaceAISettings.objects.filter(owner=owner).values_list("config", flat=True).first() or {}
    return {}


def installation_options(installation, provider=""):
    selected = provider or installation.get("provider") or "openai"
    return {"provider": selected, **installation.get("profiles", {}).get(selected, {}),
            **{field: installation[field] for field in WHISPER_FIELDS if field in installation}}


def merge_ai_options(data, saved):
    options = {field: data[field] for field in DEFAULTS}
    if data["api_key"] and not data["remove_key"]:
        options["secret"] = seal_api_key(data["api_key"])
    elif (data["provider"] and not data["remove_key"] and saved.get("secret")
          and all(saved.get(field, "") == options[field] for field in ("provider", "base_url"))):
        options["secret"] = saved["secret"]
    return options


def merge_installation(installation, data):
    provider = data["provider"] or installation_options(installation)["provider"]
    options = merge_ai_options({**data, "provider": provider}, installation_options(installation, provider))
    profiles = {**installation.get("profiles", {}), provider: {
        key: options[key] for key in ("model", "base_url", "secret") if key in options
    }}
    return {"provider": provider, "profiles": profiles, **{field: options[field] for field in WHISPER_FIELDS}}


def resolve_ai_options(options, installation=None):
    if installation is None:
        installation = installation_ai()
    fallback = installation_options(installation, options.get("provider", ""))
    provider = fallback["provider"]
    base_url = options.get("base_url") or fallback.get("base_url", "")
    matching = provider != "compatible" or base_url == fallback.get("base_url")
    model = options.get("model") or (fallback.get("model", "") if matching else "")
    secret = options.get("secret") or (fallback.get("secret") if matching else "")
    key = ""
    if secret:
        try:
            key = _cipher().decrypt(secret.encode()).decode()
        except InvalidToken as exc:
            raise RankingConfigurationError("The stored credential could not be read. Enter the API key again in Settings.") from exc
    return ranking_config(provider, model, base_url, key)


def transcription_options(options, installation):
    effective = {field: options.get(field) or installation.get(field) for field in WHISPER_FIELDS}
    parameters = options.get("vad_parameters")
    return {"whisper_model": effective["whisper_model"], "whisper_device": effective["whisper_device"] or "auto",
            "vad_filter": effective["whisper_vad"] == "on",
            "vad_parameters": parameters if parameters is not None else installation.get("vad_parameters")}


def ai_settings_data(options, installation, *, global_scope=False):
    requested = DEFAULTS | {field: options[field] for field in DEFAULTS if field in options}
    defaults = installation_options(installation)
    try:
        config = resolve_ai_options(options, installation)
        source = "saved" if options.get("secret") else "installation" if config.api_key else ""
        connection = {"provider": config.provider, "model": config.model, "base_url": config.base_url,
                      "key_source": source, "has_key": bool(config.api_key)}
    except RankingConfigurationError:
        connection = {"provider": requested["provider"], "model": requested["model"], "key_source": ""}
    return {
        "settings": requested, "connection": connection,
        "installation": {"provider": defaults["provider"], "model": defaults.get("model") or ranking_config(defaults["provider"]).model,
                         "base_url": defaults.get("base_url", "")},
        "profiles": {
            provider: {"model": profile.get("model", ""), "base_url": profile.get("base_url", ""),
                       "has_key": bool(profile.get("secret"))}
            for provider, profile in installation.get("profiles", {}).items()
        } if global_scope else {},
        "whisper": {"models": WHISPER_MODELS, "model": installation.get("whisper_model") or DEFAULT_WHISPER_MODEL,
                    "device": installation.get("whisper_device") or "auto", "vad": installation.get("whisper_vad") or "off"},
        "provider_defaults": {provider: ranking_config(provider).model for provider in PROVIDERS},
    }
