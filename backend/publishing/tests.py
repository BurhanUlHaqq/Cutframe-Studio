import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from studio.exports import job_directory
from studio.models import Job

from .config import client_config, seal, unseal
from .models import Authorization, Connection, Platform, Publication, PublishingSettings
from .oauth import exchange
from .worker import process_next


class PublishingTests(TestCase):
    def setUp(self):
        PublishingSettings.objects.create(callback_origin="https://callback.example.com", clients={
            platform: {"id": f"test-{platform}-id", "secret": seal({"value": f"test-{platform}-secret"})}
            for platform in Platform.values
        })
        self.owner = uuid4()
        session = self.client.session
        session["studio_owner"] = str(self.owner)
        session.save()
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        media_settings = override_settings(STUDIO_DATA_DIR=Path(self.media.name))
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.job = Job.objects.create(owner=self.owner, source_name="Source.mp4", clip_count=1,
                                      aspect_ratio="9:16", quality="720", status=Job.Status.COMPLETE,
                                      clips=[{"name": "clip-01", "title": "Test title", "description": "Test notes"}])
        file = job_directory(self.job.id) / "files" / "clip-01.mp4"
        file.parent.mkdir(parents=True)
        file.write_bytes(b"example video")

    def connect_fixture(self, platform):
        return Connection.objects.create(
            owner=self.owner, platform=platform, client_id=f"test-{platform}-id",
            accounts=[{"id": "123", "label": "Test creator"}],
            credentials=seal({"tokens": {"123": "test-token"}} if platform == "instagram" else {
                "access_token": "test-token", "refresh_token": "test-refresh",
                "expires": (timezone.now() + timedelta(hours=1)).timestamp(),
            }),
        )

    def payload(self, platform="youtube"):
        return {"request_id": str(uuid4()), "job_id": str(self.job.id), "clip_index": 0,
                "platform": platform, "account_id": "123", "title": "An Exact Title", "caption": "The post caption",
                "privacy": "private", "made_for_kids": "false", "confirm": True}

    def queue(self, platform):
        self.connect_fixture(platform)
        response = self.client.post(reverse("publish_posts"), self.payload(platform), content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        return Publication.objects.get(pk=response.json()["id"])

    def test_oauth_uses_single_use_state_and_requires_the_original_browser(self):
        start = self.client.post(reverse("publish_connect", args=["youtube"])).json()["url"]
        params = parse_qs(urlsplit(start).query)
        state = params["state"][0]
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(Authorization.objects.count(), 1)
        response = Client().get(reverse("publish_callback", args=["youtube"]), {"state": state, "code": "test-code"})
        self.assertTrue(response.url.startswith("http://localhost:5031/publish#"))
        self.assertEqual(parse_qs(urlsplit(response.url).fragment)["code"], ["test-code"])
        payload = {"platform": "youtube", "state": state, "code": "test-code"}
        finish = reverse("publish_finish")
        with patch("publishing.views.exchange", return_value=([{"id": "channel", "label": "My channel"}],
                                                              {"access_token": "secret-access"})) as exchange:
            self.assertEqual(Client().post(finish, payload, content_type="application/json").status_code, 400)
            exchange.assert_not_called()
            self.assertEqual(self.client.post(finish, payload, content_type="application/json").status_code, 200)
            self.assertEqual(self.client.post(finish, payload, content_type="application/json").status_code, 400)
            exchange.assert_called_once()
        self.assertNotIn("secret-access", Connection.objects.get().credentials)
        self.assertNotContains(self.client.get(reverse("publish_accounts")), "secret-access")

    def test_connect_keeps_https_callbacks_separate_from_the_local_studio(self):
        url = reverse("publish_connect", args=["tiktok"])
        self.assertEqual(self.client.post(url, HTTP_ORIGIN="http://127.0.0.1:5031").status_code, 400)
        response = self.client.post(url, HTTP_ORIGIN="http://localhost:5031")
        params = parse_qs(urlsplit(response.json()["url"]).query)
        self.assertEqual(params["redirect_uri"], ["https://callback.example.com/api/publishing/callback/tiktok/"])
        self.assertIn("video.upload", params["scope"][0])

    def test_saved_publishing_app_is_immediately_available_and_secret_is_write_only(self):
        network = reverse("publish_save_settings", args=["network"])
        transport = {"studio_origin": "http://localhost:5031", "callback_origin": "https://new.example.com"}
        self.assertEqual(Client(enforce_csrf_checks=True).post(network, transport, content_type="application/json").status_code, 403)
        invalid = self.client.post(network, {**transport, "studio_origin": "https://remote.example.com"},
                                   content_type="application/json")
        self.assertIn("studio_origin", invalid.json()["fields"])
        updated = self.client.post(network, transport, content_type="application/json")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(client_config("tiktok").redirect_uri, "https://new.example.com/api/publishing/callback/tiktok/")

        url = reverse("publish_save_settings", args=["youtube"])
        self.connect_fixture("youtube")
        secret = "a-new-developer-secret"
        response = self.client.post(url, {"client_id": "replacement", "client_secret": secret},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, secret)
        self.assertNotIn(secret, str(PublishingSettings.objects.get().clients))
        self.assertEqual(client_config("youtube").secret, secret)
        accounts = self.client.get(reverse("publish_accounts")).json()["platforms"][0]
        self.assertTrue(accounts["configured"])
        self.assertEqual(accounts["accounts"], [])
        self.client.post(url, {"client_id": "replacement"}, content_type="application/json")
        self.assertEqual(client_config("youtube").secret, secret)
        self.client.post(url, {"client_id": "replacement", "remove_secret": True}, content_type="application/json")
        self.assertEqual(client_config("youtube").secret, "")

    def test_platform_oauth_exchanges_resolve_accounts_and_keep_tokens_server_side(self):
        def router(request):
            path = request.url.path
            if path in {"/token", "/v2/oauth/token/"}:
                form = parse_qs(request.content.decode())
                self.assertEqual(form["code"], ["test-code"])
                if path == "/token":
                    self.assertEqual(form["code_verifier"], ["verifier"])
                else:
                    self.assertEqual(form["client_key"], ["test-tiktok-id"])
                return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh",
                                                "scope": "user.info.basic,video.upload", "open_id": "123"})
            if path == "/youtube/v3/channels":
                self.assertEqual(request.url.params["mine"], "true")
                return httpx.Response(200, json={"items": [{"id": "123", "snippet": {"title": "Creator"}}]})
            if path == "/v2/user/info/":
                return httpx.Response(200, json={"data": {"user": {"open_id": "123", "display_name": "Creator"}}})
            if path.endswith("/oauth/access_token"):
                return httpx.Response(200, json={"access_token": "facebook-user"})
            self.assertTrue(path.endswith("/me/accounts"))
            self.assertEqual(request.headers["Authorization"], "Bearer facebook-user")
            return httpx.Response(200, json={"data": [{"name": "Page", "access_token": "page",
                                                       "instagram_business_account": {"id": "123", "username": "Creator"}}]})

        for platform in ("youtube", "tiktok", "instagram"):
            client = httpx.Client(transport=httpx.MockTransport(router))
            with self.subTest(platform=platform), patch("publishing.oauth.httpx.Client", return_value=client):
                accounts, tokens = exchange(platform, client_config(platform), "test-code", "verifier")
                self.assertEqual(accounts, [{"id": "123", "label": "Creator"}])
                self.assertEqual(tokens.get("access_token") or tokens["tokens"]["123"],
                                 "page" if platform == "instagram" else "access")

    def test_scheduled_posts_are_idempotent_private_to_workspace_and_cancelable(self):
        self.connect_fixture("youtube")
        url = reverse("publish_posts")
        payload = self.payload()
        self.assertEqual(self.client.post(url, {**payload, "confirm": False}, content_type="application/json").status_code, 400)
        payload["scheduled_at"] = (timezone.now() + timedelta(minutes=30)).isoformat()
        response = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(response.status_code, 201)
        duplicate = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(response.json()["id"], duplicate.json()["id"])
        self.assertFalse(process_next())
        self.assertEqual(Client().get(url).json()["posts"], [])
        cancel = reverse("publish_action", args=[response.json()["id"], "cancel"])
        self.assertEqual(Client().post(cancel).status_code, 404)
        self.assertEqual(self.client.post(cancel).json()["status"], "cancelled")

    def test_highlight_reels_get_youtube_chapters_and_platform_duration_checks(self):
        chapters = "00:00 Setup\n10:00 Main event\n20:00 Result"
        clip = self.job.clips[0]
        clip.update(start_time=0, end_time=1800, chapter_text=chapters)
        self.job.save(update_fields=["clips"])
        for platform in ("tiktok", "instagram"):
            self.connect_fixture(platform)
            response = self.client.post(reverse("publish_posts"), self.payload(platform), content_type="application/json")
            self.assertEqual(response.status_code, 400)
            self.assertIn("minutes", response.json()["error"])
        self.assertEqual(self.queue("youtube").options["caption"], f"The post caption\n\n{chapters}")

    def run_with(self, router):
        client = httpx.Client(transport=httpx.MockTransport(router))
        with patch("publishing.worker.httpx.Client", return_value=client):
            self.assertTrue(process_next())

    def test_youtube_uses_approved_title_audience_and_visibility_and_sends_video_bytes(self):
        post = self.queue("youtube")
        calls = []

        def router(request):
            calls.append(request.method)
            if request.method == "POST":
                data = json.loads(request.content)
                self.assertEqual(data["snippet"]["title"], "An Exact Title")
                self.assertEqual(data["snippet"]["categoryId"], "22")
                self.assertEqual(data["status"]["privacyStatus"], "private")
                self.assertFalse(data["status"]["selfDeclaredMadeForKids"])
                return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/test"})
            self.assertEqual(request.read(), b"example video")
            self.assertEqual(request.headers["Content-Range"], "bytes 0-12/13")
            return httpx.Response(201, json={"id": "video123", "status": {"privacyStatus": "private"}})

        self.run_with(router)
        post.refresh_from_db()
        self.assertEqual(calls, ["POST", "PUT"])
        self.assertEqual(post.status, "complete")
        self.assertEqual(post.url, "https://www.youtube.com/watch?v=video123")

    def test_youtube_waits_for_processing_and_uploads_the_approved_jpg_without_resending_video(self):
        files = job_directory(self.job.id) / "files"
        filename = "clip-01-cover-0123456789abcdef01234567.jpg"
        cover = b"\xff\xd8edited thumbnail\xff\xd9"
        (files / filename).write_bytes(cover)
        self.job.clips[0]["thumbnail_file"] = filename
        self.job.save(update_fields=["clips"])
        post = self.queue("youtube")
        # Later edits must leave an already approved upload pinned to its selected JPG.
        self.job.clips[0].pop("thumbnail_file")
        self.job.save(update_fields=["clips"])
        (files / "clip-01.jpg").write_bytes(b"other cover")

        def upload(request):
            if request.method == "POST":
                return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/test"})
            self.assertEqual(request.method, "PUT")
            self.assertEqual(request.read(), b"example video")
            return httpx.Response(201, json={"id": "video123", "status": {"privacyStatus": "private"}})

        self.run_with(upload)
        post.refresh_from_db()
        self.assertEqual(post.status, Publication.Status.PROCESSING)
        self.assertEqual(unseal(post.transfer)["video_id"], "video123")
        self.assertEqual(post.url, "https://www.youtube.com/watch?v=video123")
        (files / "clip-01.mp4").unlink()

        def processing(request):
            self.assertEqual((request.method, request.url.path), ("GET", "/youtube/v3/videos"))
            self.assertEqual(request.url.params["id"], "video123")
            return httpx.Response(200, json={"items": [{"processingDetails": {"processingStatus": "processing"}}]})

        Publication.objects.filter(pk=post.pk).update(next_at=timezone.now())
        self.run_with(processing)
        post.refresh_from_db()
        self.assertEqual(post.status, Publication.Status.PROCESSING)

        def ready(request):
            if request.method == "GET":
                return httpx.Response(200, json={"items": [{"processingDetails": {"processingStatus": "succeeded"}}]})
            self.assertEqual((request.method, request.url.path), ("POST", "/upload/youtube/v3/thumbnails/set"))
            self.assertEqual(request.url.params["videoId"], "video123")
            self.assertEqual(request.url.params["uploadType"], "media")
            self.assertEqual(request.headers["Content-Type"], "image/jpeg")
            self.assertEqual(request.read(), cover)
            return httpx.Response(200, json={"items": [{}]})

        Publication.objects.filter(pk=post.pk).update(next_at=timezone.now())
        self.run_with(ready)
        post.refresh_from_db()
        self.assertEqual(post.status, Publication.Status.COMPLETE)
        self.assertEqual(post.options["thumbnail_state"], "applied")
        self.assertEqual(post.transfer, "")

    def test_completed_youtube_upload_can_retry_a_thumbnail_after_a_channel_error(self):
        post = self.queue("youtube")
        post.status = Publication.Status.COMPLETE
        post.url = "https://www.youtube.com/watch?v=video123"
        post.save(update_fields=["status", "url"])
        action = reverse("publish_action", args=[post.id, "thumbnail"])
        self.assertEqual(Client().post(action).status_code, 404)
        self.assertEqual(self.client.post(action).status_code, 400)
        files = job_directory(self.job.id) / "files"
        (files / "clip-01.jpg").write_bytes(b"\xff\xd8thumbnail\xff\xd9")
        self.assertEqual(self.client.post(action).status_code, 200)
        self.assertEqual(self.client.post(action).status_code, 409)

        def rejected(request):
            if request.method == "GET":
                return httpx.Response(200, json={"items": [{"status": {"uploadStatus": "processed"}}]})
            self.assertTrue(request.url.path.endswith("/thumbnails/set"))
            return httpx.Response(403)

        self.run_with(rejected)
        post.refresh_from_db()
        self.assertEqual(post.status, Publication.Status.COMPLETE)
        self.assertEqual(post.options["thumbnail_state"], "failed")
        self.assertIn("custom thumbnail access", post.message)
        self.assertEqual(post.url, "https://www.youtube.com/watch?v=video123")
        refresh = self.client.post(action)
        self.assertEqual(refresh.status_code, 200)
        self.assertEqual(refresh.json()["status"], "processing")

    def test_tiktok_reports_inbox_then_checks_status_without_sending_again(self):
        post = self.queue("tiktok")

        def router(request):
            if request.url.path.endswith("/init/"):
                return httpx.Response(200, json={"data": {"publish_id": "v_inbox_file~v2.123",
                    "upload_url": "https://open-upload.tiktokapis.com/media?secret=1"}})
            if request.method == "PUT":
                self.assertEqual(request.read(), b"example video")
                return httpx.Response(201)
            return httpx.Response(200, json={"data": {"status": "SEND_TO_USER_INBOX"}})

        self.run_with(router)
        post.refresh_from_db()
        self.assertEqual(post.status, "inbox")
        self.assertNotIn("open-upload", post.transfer)
        self.assertEqual(unseal(post.transfer)["id"], "v_inbox_file~v2.123")
        response = self.client.post(reverse("publish_action", args=[post.id, "check"]))
        self.assertEqual(response.status_code, 200)

        def posted(request):
            self.assertTrue(request.url.path.endswith("/fetch/"))
            return httpx.Response(200, json={"data": {"status": "PUBLISH_COMPLETE"}})

        self.run_with(posted)
        post.refresh_from_db()
        self.assertEqual(post.status, "complete")

    def test_instagram_resumes_status_after_ambiguous_publish_without_reposting(self):
        self.job.clips[0].update(thumbnail_design={"frame": 1}, thumbnail_options=[{"time": 0.1}, {"time": 2.75}])
        self.job.save(update_fields=["clips"])
        post = self.queue("instagram")
        phases = iter(["IN_PROGRESS", "FINISHED", "PUBLISHED"])
        sends = []

        def router(request):
            if request.url.host == "rupload.facebook.com":
                self.assertEqual(request.read(), b"example video")
                return httpx.Response(200, json={"success": True})
            if request.url.path.endswith("/media"):
                self.assertEqual(json.loads(request.content)["thumb_offset"], "2750")
                return httpx.Response(200, json={"id": "ig123"})
            if request.url.path.endswith("/media_publish"):
                sends.append(request.url.path)
                raise httpx.ReadTimeout("No acknowledgement", request=request)
            return httpx.Response(200, json={"status_code": next(phases)})

        for _ in range(3):
            self.run_with(router)
            Publication.objects.filter(pk=post.pk).update(next_at=timezone.now())
        post.refresh_from_db()
        self.assertEqual(post.status, "complete")
        self.assertEqual(len(sends), 1)
