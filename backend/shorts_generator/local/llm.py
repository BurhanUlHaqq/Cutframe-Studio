"""Configuration and clients for transcript ranking."""

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit


PROVIDERS = {
    "openai": ("OpenAI", "gpt-4o-mini"),
    "gemini": ("Google Gemini", "gemini-2.5-flash"),
    "compatible": ("OpenAI-compatible", ""),
}
OPENAI_URL = "https://api.openai.com/v1"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
PLACEHOLDER = re.compile(r"^(?:your_[\w-]*key_here|x{3,}|change[-_ ]?me|replace[-_ ]?me|placeholder|<[^>]+>)$", re.I)
NON_CHAT_MODEL = re.compile(
    r"(?:^|-)(?:audio|realtime|transcribe|whisper|tts|embedding|moderation|image|dall-e|sora|computer-use)(?:-|$)",
    re.I,
)


class RankingConfigurationError(ValueError):
    pass


class RankingModelUnavailable(ValueError):
    pass


def clean_key(value):
    value = value.strip()
    return "" if PLACEHOLDER.fullmatch(value) else value


def normalize_endpoint(value, *, browser=False, trusted_url=""):
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise RankingConfigurationError("Enter a valid HTTP or HTTPS API base URL.") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or port == 0 or parsed.netloc.endswith(":")
            or parsed.username is not None or re.search(r"[\s\\?#]", value)):
        raise RankingConfigurationError("Enter an HTTP or HTTPS API base URL without credentials, query, or fragment.")
    if browser and parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
        if value != trusted_url:
            raise RankingConfigurationError("Use HTTPS for a remote API and HTTP for a local model server.")
    return value


@dataclass(frozen=True)
class RankingConfig:
    provider: str
    model: str
    base_url: str = ""
    api_key: str = field(default="", repr=False)

    def validate(self, *, require_model=True):
        if require_model and not self.model:
            raise RankingConfigurationError("Enter the ranking model ID in Settings → AI models.")
        if self.provider == "compatible":
            normalize_endpoint(self.base_url)
        elif not self.api_key:
            label, _ = PROVIDERS[self.provider]
            raise RankingConfigurationError(f"Add a {label} API key in Settings → AI models.")


def ranking_config(provider="", model="", base_url="", api_key=""):
    provider = provider or "openai"
    if provider not in PROVIDERS:
        raise RankingConfigurationError("Choose OpenAI, Google Gemini, or an OpenAI-compatible provider.")
    _, default_model = PROVIDERS[provider]
    base_url = base_url.strip().rstrip("/") if provider == "compatible" else OPENAI_URL if provider == "openai" else ""
    return RankingConfig(provider, model.strip() or default_model, base_url, clean_key(api_key))


@contextmanager
def _client(config, timeout, *, require_model=True):
    config.validate(require_model=require_model)
    if config.provider == "gemini":
        from google import genai
        from google.genai import types

        options = types.HttpOptions(timeout=timeout * 1000, retry_options=types.HttpRetryOptions(attempts=1))
        with genai.Client(api_key=config.api_key, http_options=options, vertexai=False) as client:
            yield client
    else:
        import httpx
        from openai import OpenAI

        with httpx.Client(timeout=timeout, follow_redirects=False) as transport:
            with OpenAI(api_key=config.api_key or "local", base_url=config.base_url, timeout=timeout,
                        max_retries=0, http_client=transport) as client:
                yield client


@contextmanager
def ranking_client(config):
    with _client(config, 180) as client:
        def rank(prompt):
            if config.provider == "gemini":
                response = client.models.generate_content(
                    model=config.model, contents=prompt,
                    config={"response_mime_type": "application/json", "max_output_tokens": 8192},
                )
                return response.text or ""
            response = client.chat.completions.create(
                model=config.model, messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content or ""

        yield rank


def check_connection(config):
    """Verify model visibility without running a prompt."""
    with _client(config, 10) as client:
        if config.provider == "gemini":
            client.models.get(model=config.model)
        elif not any(model.id == config.model for model in client.models.list()):
            raise RankingModelUnavailable("The endpoint responded, but its model list does not include that model ID.")


def list_ranking_models(config):
    """Fetch model IDs through the provider's metadata API."""
    with _client(config, 10, require_model=False) as client:
        if config.provider == "gemini":
            model_ids = {
                model.name.removeprefix("models/") for model in client.models.list()
                if model.name and "generateContent" in (model.supported_actions or ())
            }
        else:
            model_ids = {model.id for model in client.models.list() if model.id}

    return sorted(
        (model_id for model_id in model_ids if 0 < len(model_id) <= 200
         and (config.provider == "compatible" or not NON_CHAT_MODEL.search(model_id))),
        key=lambda model_id: (model_id.casefold(), model_id),
    )


def call_local_llm(prompt):
    with ranking_client(ranking_config()) as rank:
        return rank(prompt)
