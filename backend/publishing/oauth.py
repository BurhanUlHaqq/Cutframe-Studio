import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from django.utils import timezone

from .config import PublishError, client_config, seal, unseal
from .http import bearer, read_response, request
from .models import Connection, Platform


GOOGLE_SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
TIKTOK_SCOPES = "user.info.basic,video.upload"
META_SCOPES = "pages_show_list,pages_read_engagement,instagram_basic,instagram_content_publish"


def state_digest(state):
    return hashlib.sha256(state.encode()).hexdigest()


def authorize_url(platform, config, state, verifier):
    params = {"redirect_uri": config.redirect_uri, "response_type": "code", "state": state}
    if platform == Platform.YOUTUBE:
        base = "https://accounts.google.com/o/oauth2/v2/auth"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params.update(client_id=config.client_id, scope=GOOGLE_SCOPES, prompt="consent select_account",
                      access_type="offline", code_challenge=challenge, code_challenge_method="S256")
    elif platform == Platform.TIKTOK:
        base = "https://www.tiktok.com/v2/auth/authorize/"
        params.update(client_key=config.client_id, scope=TIKTOK_SCOPES)
    else:
        base = f"https://www.facebook.com/{config.graph_version}/dialog/oauth"
        params["client_id"] = config.client_id
        if config.login_config:
            params["config_id"] = config.login_config
        else:
            params["scope"] = META_SCOPES
    return f"{base}?{urlencode(params)}"


def token_request(client, platform, config, **values):
    if platform == Platform.TIKTOK:
        return read_response(request(client, "POST", "https://open.tiktokapis.com/v2/oauth/token/",
                                     data={"client_key": config.client_id, "client_secret": config.secret, **values}))
    return read_response(request(client, "POST", "https://oauth2.googleapis.com/token",
                                 data={"client_id": config.client_id, "client_secret": config.secret, **values}))


def graph_base(version):
    return f"https://graph.facebook.com/{version}"


def exchange(platform, config, code, verifier):
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        if platform == Platform.INSTAGRAM:
            endpoint = f"{graph_base(config.graph_version)}/oauth/access_token"
            credentials = {"client_id": config.client_id, "client_secret": config.secret}
            temporary = read_response(request(client, "GET", endpoint, params={
                **credentials, "code": code, "redirect_uri": config.redirect_uri,
            }))
            token = read_response(request(client, "GET", endpoint, params={
                **credentials, "grant_type": "fb_exchange_token", "fb_exchange_token": temporary["access_token"],
            }))
            return instagram_accounts(client, token["access_token"], config.graph_version)

        params = {"code": code, "redirect_uri": config.redirect_uri, "grant_type": "authorization_code"}
        if platform == Platform.YOUTUBE:
            params["code_verifier"] = verifier
        token = token_request(client, platform, config, **params)
        access = token.get("access_token")
        if not access or not token.get("refresh_token"):
            raise PublishError("The platform did not grant offline publishing access. Try connecting again.")
        if platform == Platform.YOUTUBE:
            data = read_response(request(client, "GET", "https://www.googleapis.com/youtube/v3/channels",
                                         params={"part": "snippet", "mine": "true"}, headers=bearer(access)))
            items = data.get("items", [])
            if len(items) != 1:
                raise PublishError("Sign in to a single YouTube channel that can upload videos.")
            account = {"id": items[0]["id"], "label": items[0]["snippet"]["title"]}
        else:
            scopes = set(token.get("scope", "").split(","))
            if "video.upload" not in scopes:
                raise PublishError("Grant TikTok's video upload permission before connecting.")
            profile = read_response(request(client, "GET", "https://open.tiktokapis.com/v2/user/info/",
                                            params={"fields": "open_id,display_name"}, headers=bearer(access)),
                                    tiktok=True)["user"]
            if profile["open_id"] != token.get("open_id"):
                raise PublishError("TikTok returned an unexpected account. Reconnect and try again.")
            account = {"id": profile["open_id"], "label": profile["display_name"]}
        return [account], timed_token(token)


def instagram_accounts(client, user_token, version):
    endpoint = f"{graph_base(version)}/me/accounts"
    params = {"fields": "name,access_token,instagram_business_account{id,username}", "limit": 100}
    pages = read_response(request(client, "GET", endpoint, params=params, headers=bearer(user_token)))
    accounts, tokens, cursors = {}, {}, set()
    while True:
        for page in pages.get("data", []):
            account = page.get("instagram_business_account") or {}
            if account.get("id") and page.get("access_token"):
                account_id = str(account["id"])
                accounts[account_id] = {"id": account_id, "label": account.get("username") or page["name"]}
                tokens[account_id] = page["access_token"]
        paging = pages.get("paging", {})
        after = (paging.get("cursors") or {}).get("after")
        if not paging.get("next") or not after or after in cursors:
            break
        cursors.add(after)
        pages = read_response(request(client, "GET", endpoint, params={**params, "after": after}, headers=bearer(user_token)))
    if not accounts:
        raise PublishError("Link an Instagram professional account to a Facebook Page you manage, then reconnect.")
    return list(accounts.values()), {"tokens": tokens}


def timed_token(values):
    return {
        "access_token": values["access_token"],
        "refresh_token": values.get("refresh_token", ""),
        "expires": (timezone.now() + timedelta(seconds=int(values.get("expires_in", 3600)))).timestamp(),
    }


def access_token(client, connection, account_id):
    config = client_config(connection.platform)
    if config.client_id != connection.client_id or not config.secret:
        raise PublishError("The platform app credentials changed. Reconnect the account.")
    data = unseal(connection.credentials)
    if connection.platform == Platform.INSTAGRAM:
        return data["tokens"][account_id]
    if data["expires"] > timezone.now().timestamp() + 90:
        return data["access_token"]
    renewed = token_request(client, connection.platform, config, grant_type="refresh_token",
                            refresh_token=data["refresh_token"])
    if not renewed.get("access_token"):
        raise PublishError("The platform session expired. Reconnect the account.")
    data = timed_token({"refresh_token": data["refresh_token"], **renewed})
    if not Connection.objects.filter(pk=connection.pk, credentials=connection.credentials).update(
        credentials=seal(data), updated_at=timezone.now(),
    ):
        raise PublishError("The connected account changed. Review the destination before retrying.")
    return data["access_token"]
