import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from .models import Platform, PublishingSettings


class PublishError(Exception):
    def __init__(self, message, *, retry=False, review=False):
        super().__init__(message)
        self.retry = retry
        self.review = review


@dataclass(frozen=True)
class ClientConfig:
    client_id: str
    secret: str = field(repr=False)
    redirect_uri: str
    graph_version: str
    login_config: str


def origin(value, *, public=False):
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return ""
    if (parsed.scheme not in ({"https"} if public else {"http", "https"})
            or not parsed.hostname or parsed.username is not None or parsed.path
            or port == 0 or parsed.netloc.endswith(":") or re.search(r"[\s\\?#]", value)):
        return ""
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if public == loopback:
        return ""
    if public:
        try:
            URLValidator(schemes=["https"])(value)
        except ValidationError:
            return ""
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def publishing_settings():
    return PublishingSettings.objects.filter(pk=1).first() or PublishingSettings()


def client_config(platform, setup=None):
    if setup is None:
        setup = publishing_settings()
    host = setup.studio_origin if platform == Platform.YOUTUBE else setup.callback_origin
    client = setup.clients.get(platform, {})
    try:
        secret = unseal(client.get("secret", "")).get("value", "")
    except PublishError:
        secret = ""
    return ClientConfig(
        client.get("id", ""), secret, f"{host}/api/publishing/callback/{platform}/" if host else "",
        setup.meta_api_version, setup.instagram_login_config,
    )


def cipher():
    key = hmac.digest(settings.SECRET_KEY.encode(), b"cutframe-publishing-v1", hashlib.sha256)
    return Fernet(base64.urlsafe_b64encode(key))


def seal(value):
    return cipher().encrypt(json.dumps(value).encode()).decode()


def unseal(value):
    if not value:
        return {}
    try:
        return json.loads(cipher().decrypt(value.encode()))
    except InvalidToken as exc:
        raise PublishError("Local publishing credentials could not be read. Reconnect the account.") from exc
