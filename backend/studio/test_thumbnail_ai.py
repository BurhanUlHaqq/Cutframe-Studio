import base64
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

import httpx
from PIL import Image
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from publishing.models import Platform
from publishing.thumbnails import cover_options

from .exports import job_directory
from .models import Job, WorkspaceThumbnailSettings


def image(color):
    output = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(output, format="JPEG")
    return output.getvalue()


class AIThumbnailTests(TestCase):
    def test_generation_and_regeneration_replace_zip_cover_and_keep_previous_image_on_failure(self):
        settings = reverse("thumbnail_ai_settings")
        self.client.get(reverse("session"))
        owner = self.client.session["studio_owner"]
        secret = "image-provider-test-credential"
        posted = self.client.post(settings, {"provider": "compatible", "model": "flux-test",
                                             "base_url": "http://host.docker.internal:7860/v1",
                                             "api_key": secret, "automatic": False}, content_type="application/json")
        self.assertEqual(posted.status_code, 200, posted.content)
        self.assertTrue(posted.json()["ready"])
        self.assertTrue(posted.json()["has_key"])
        self.assertNotIn(secret, posted.content.decode())
        self.assertNotIn(secret, str(WorkspaceThumbnailSettings.objects.get(pk=owner).config))
        self.assertFalse(Client().get(settings).json()["ready"])

        with TemporaryDirectory() as temporary, override_settings(STUDIO_DATA_DIR=Path(temporary)):
            clip = {"name": "clip-01", "title": "Game Point", "embedded_title": "Game Point",
                    "description": "The winner takes the last point."}
            job = Job.objects.create(owner=owner, source_name="Match.mp4", status=Job.Status.COMPLETE, clips=[clip])
            files = job_directory(job.id) / "files"
            files.mkdir(parents=True)
            (files / "clip-01.mp4").write_bytes(b"video fixture")
            (files / "clip-01.jpg").write_bytes(image("green"))
            url = self.client.get(reverse("detail", args=[job.id])).json()["clips"][0]["thumbnail_generate_url"]
            self.assertEqual(Client().post(url, {}, content_type="application/json").status_code, 404)

            colors = iter(("red", "blue", None))
            directions = []

            def respond(request):
                self.assertEqual(str(request.url), "http://host.docker.internal:7860/v1/images/generations")
                self.assertEqual(request.headers["Authorization"], f"Bearer {secret}")
                payload = json.loads(request.content)
                self.assertEqual(payload["model"], "flux-test")
                self.assertEqual(payload["response_format"], "b64_json")
                self.assertIn("Game Point", payload["prompt"])
                directions.append(payload["prompt"])
                color = next(colors)
                return (httpx.Response(429) if color is None else
                        httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(image(color)).decode()}]}))

            request_client = httpx.Client
            with patch("studio.thumbnail_ai.httpx.Client", side_effect=lambda **_: request_client(transport=httpx.MockTransport(respond))):
                first = self.client.post(url, {}, content_type="application/json")
                self.assertEqual(first.status_code, 200, first.content)
                first_cover = first.json()["clips"][0]["thumbnail_url"]
                result = self.client.post(url, {"direction": "Use a blue palette"}, content_type="application/json")
                self.assertEqual(result.status_code, 200, result.content)
                updated = result.json()["clips"][0]
                self.assertIn("Use a blue palette", directions[1])
                self.assertEqual(updated["thumbnail_source"], "ai")
                self.assertEqual(updated["thumbnail_model"], "flux-test")
                self.assertNotEqual(updated["thumbnail_url"], first_cover)
                cover = b"".join(self.client.get(updated["thumbnail_url"]).streaming_content)
                with Image.open(io.BytesIO(cover)) as published:
                    self.assertEqual(published.size, (1080, 1920))
                    red, _, blue = published.getpixel((500, 500))
                    self.assertGreater(blue, red + 200)
                archive = self.client.get(result.json()["archive_url"])
                with ZipFile(io.BytesIO(b"".join(archive.streaming_content))) as zipped:
                    self.assertEqual(zipped.read("01/Game Point.jpg"), cover)
                job.refresh_from_db()
                approved = cover_options(job, 0, Platform.YOUTUBE)
                self.assertEqual((files / approved["thumbnail_file"]).read_bytes(), cover)
                failed = self.client.post(url, {}, content_type="application/json")
                self.assertEqual(failed.status_code, 422)
                self.assertIn("quota", failed.json()["error"])
                job.refresh_from_db()
                self.assertEqual(job.clips[0]["thumbnail_file"], approved["thumbnail_file"])
