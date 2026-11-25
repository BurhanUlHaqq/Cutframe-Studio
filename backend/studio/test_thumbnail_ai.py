import base64
import io
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

import httpx
from PIL import Image
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from publishing.models import Platform
from publishing.thumbnails import cover_options

from .exports import job_directory
from .models import Job, WorkspaceThumbnailSettings
from .thumbnail_ai import ImageProvider, make_ai_cover
from .worker import process_next_job


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
            job = Job.objects.create(owner=owner, source_name="Match.mp4", status=Job.Status.COMPLETE,
                                     clip_count=1, aspect_ratio="9:16", quality="720", clips=[clip])
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

    def test_worker_can_generate_automatically_before_creating_the_zip(self):
        self.client.get(reverse("session"))
        owner = self.client.session["studio_owner"]
        response = self.client.post(reverse("thumbnail_ai_settings"), {
            "provider": "compatible", "model": "fixture", "base_url": "http://127.0.0.1:7860/v1", "automatic": True,
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        with TemporaryDirectory() as temporary, override_settings(STUDIO_DATA_DIR=Path(temporary)):
            job = Job.objects.create(owner=owner, source_name="Game.mp4", editing_style="gameplay",
                                     clip_count=1, aspect_ratio="9:16", quality="720")
            work = job_directory(job.id) / "work"
            work.mkdir(parents=True)
            video = work / "clip.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=180x320:r=15:d=1",
                "-c:v", "libx264", str(video),
            ], check=True, capture_output=True)
            fixture = work / "cover.jpg"
            fixture.write_bytes(image("green"))
            new_cover = image("orange")
            with patch("studio.worker.generate_shorts", return_value=[{
                "clip_url": str(video), "thumbnail_path": str(fixture), "title": "Game Point",
                "score": 90, "start_time": 0, "end_time": 1,
            }]), patch("studio.worker.make_ai_cover", return_value=new_cover) as generate:
                process_next_job()
            generate.assert_called_once()
            job.refresh_from_db()
            self.assertEqual(job.status, Job.Status.COMPLETE, job.error)
            self.assertEqual(job.clips[0]["thumbnail_source"], "ai")
            self.assertIn("Generating AI cover 1 of 1", [event["message"] for event in job.events])
            archive = self.client.get(reverse("archive", args=[job.id]))
            with ZipFile(io.BytesIO(b"".join(archive.streaming_content))) as zipped:
                self.assertEqual(zipped.read("01/Game Point.jpg"), new_cover)


class OpenAIImageProtocolTests(SimpleTestCase):
    def test_openai_sends_reference_and_uses_returned_art(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.jpg"
            reference.write_bytes(image("green"))
            config = ImageProvider("openai", "gpt-image-2", "https://api.openai.com/v1", "low", "fixture-key")

            def respond(request):
                self.assertEqual(str(request.url), "https://api.openai.com/v1/images/edits")
                self.assertEqual(request.headers["Authorization"], "Bearer fixture-key")
                self.assertIn(b'name="image[]"; filename="reference.jpg"', request.content)
                self.assertIn(b"864x1536", request.content)
                return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(image("blue")).decode()}]})

            request_client = httpx.Client
            with patch("studio.thumbnail_ai.httpx.Client", return_value=request_client(transport=httpx.MockTransport(respond))):
                result = make_ai_cover(config, {"title": "Game Point", "description": "The deciding point."},
                                       "9:16", root, reference=reference)
            with Image.open(io.BytesIO(result)) as generated:
                self.assertEqual(generated.size, (1080, 1920))
                self.assertGreater(generated.getpixel((500, 500))[2], 230)
