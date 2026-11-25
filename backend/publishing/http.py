from urllib.parse import urlsplit

import httpx

from .config import PublishError


def request(client, method, url, **kwargs):
    try:
        return client.request(method, url, **kwargs)
    except httpx.RequestError as exc:
        raise PublishError("The platform connection was interrupted.", retry=True) from exc


def read_response(response, *, tiktok=False):
    if response.status_code in {401, 403}:
        raise PublishError("The platform denied access. Check app permissions or reconnect the account.")
    if response.status_code == 429 or response.status_code >= 500:
        raise PublishError("The platform is rate-limited or temporarily unavailable.", retry=True)
    if not response.is_success:
        raise PublishError(f"The platform rejected the request (HTTP {response.status_code}). Check the account and media requirements.")
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError()
    except ValueError as exc:
        raise PublishError("The platform returned an unexpected response.", retry=True) from exc
    if tiktok:
        code = (data.get("error") or {}).get("code", "ok")
        if code != "ok":
            if code in {"rate_limit_exceeded", "internal_error"}:
                raise PublishError("TikTok is temporarily unavailable.", retry=True)
            raise PublishError("TikTok rejected the request. Check account permissions, upload limits, and media format.")
        return data.get("data", {})
    return data


def upload_url(value, hosts):
    try:
        parts = urlsplit(value)
        valid = parts.scheme == "https" and parts.hostname in hosts and parts.port in {None, 443} and parts.username is None
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise PublishError("The platform returned an unexpected upload address.")
    return value


def bearer(token):
    return {"Authorization": f"Bearer {token}"}
