import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from shorts_generator.control import RenderCancelled

from .exports import job_directory
from .models import Job
from .source_reuse import stage_alternate_source
from .uploads import uploaded_source
from .worker import process_next_job


class AlternateRenderTests(TestCase):
    def setUp(self):
        self.data = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(override_settings(STUDIO_DATA_DIR=self.data))
        self.client.get(reverse("session"))

    def alternate(self, job_id, **options):
        return self.client.post(reverse("create_alternate", args=[job_id]), options, content_type="application/json")

    def media(self):
        path = self.data / "fixture.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=192x108:r=12:d=10",
                        "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True, capture_output=True)
        return path.read_bytes()

    def uploaded(self, content):
        response = self.client.post(reverse("upload_job"), {
            "video": SimpleUploadedFile("Original video.mp4", content), "studio_mode": "shorts",
            "quality": "480", "editing_style": "gameplay", "clip_count": 1, "caption_style": "off", "language": "en",
        })
        self.assertEqual(response.status_code, 201, response.content)
        return Job.objects.get(pk=response.json()["id"])

    def delete_render(self, job):
        response = self.client.post(reverse("delete_renders"), {"ids": [str(job.id)]}, content_type="application/json")
        return response.json()

    def test_upload_can_render_both_directions_and_survive_source_removal(self):
        media = self.media()
        original = self.uploaded(media)
        self.assertTrue(process_next_job())
        original.refresh_from_db()
        self.assertEqual(original.status, Job.Status.COMPLETE, original.error)
        saved_clips = original.clips
        detail = self.client.get(reverse("detail", args=[original.id])).json()
        self.assertEqual(detail["language"], "en")

        response = self.alternate(original.id, highlight_minutes=2, highlight_method="activity")
        self.assertEqual(response.status_code, 201, response.content)
        reel = Job.objects.get(pk=response.json()["id"])
        self.assertEqual((reel.studio_mode, reel.aspect_ratio, reel.highlight_minutes), ("highlights", "16:9", 2))
        self.assertEqual(reel.source_render_id, original.id)
        self.assertEqual(reel.source_name, original.source_name)
        self.assertEqual(reel.caption_style, "off")
        self.assertEqual(response.json()["language"], "en")
        self.assertFalse(uploaded_source(reel.id).exists())
        row = next(row for row in self.client.get(reverse("render_library")).json()["renders"] if row["id"] == str(original.id))
        self.assertTrue(row["source_in_use"])
        self.assertIn("Another render", self.delete_render(original)["blocked"][0]["reason"])

        self.assertTrue(process_next_job())
        reel.refresh_from_db()
        original.refresh_from_db()
        self.assertEqual(reel.status, Job.Status.COMPLETE, reel.error)
        self.assertEqual(original.clips, saved_clips)
        self.assertEqual(uploaded_source(reel.id).read_bytes(), media)
        self.assertEqual(self.client.get(reverse("archive", args=[reel.id])).status_code, 200)
        self.assertEqual(self.delete_render(original)["removed"][0]["id"], str(original.id))
        reel.refresh_from_db()
        self.assertIsNone(reel.source_render_id)

        clips = self.alternate(reel.id, editing_style="gameplay", aspect_ratio="1:1", clip_count=2)
        self.assertEqual(clips.status_code, 201, clips.content)
        self.assertEqual((clips.json()["studio_mode"], clips.json()["aspect_ratio"]), ("shorts", "1:1"))
        self.assertTrue(process_next_job())
        round_trip = Job.objects.get(pk=clips.json()["id"])
        self.assertEqual(round_trip.status, Job.Status.COMPLETE, round_trip.error)
        self.assertEqual(uploaded_source(round_trip.id).read_bytes(), media)

    def test_youtube_reuses_a_complete_download_at_the_same_quality(self):
        response = self.client.post(reverse("jobs"), {"source_url": "https://youtu.be/abcdefghijk", "quality": "480",
                                                     "caption_style": "off"}, content_type="application/json")
        original = Job.objects.get(pk=response.json()["id"])
        original.status = Job.Status.COMPLETE
        original.save(update_fields=["status"])
        source = job_directory(original.id) / "work" / "source_abcdefghijk.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(self.media())

        response = self.alternate(original.id, highlight_method="activity")
        self.assertEqual(response.status_code, 201, response.content)
        derived = Job.objects.get(pk=response.json()["id"])
        with patch("shorts_generator.local.downloader._import_ytdlp") as download:
            self.assertTrue(process_next_job())
        download.assert_not_called()
        derived.refresh_from_db()
        self.assertEqual(derived.status, Job.Status.COMPLETE, derived.error)
        self.assertIn("Reusing the saved YouTube download", [event["message"] for event in derived.events])

        new_quality = self.alternate(original.id, highlight_method="activity", quality="720").json()
        refreshed = Job.objects.get(pk=new_quality["id"])
        work = job_directory(refreshed.id) / "work"
        stage_alternate_source(refreshed, work, lambda *args, **kwargs: None)
        self.assertFalse((work / source.name).exists())
        self.assertEqual(refreshed.source_url, original.source_url)
        self.assertTrue(self.delete_render(original)["removed"])
        refreshed.refresh_from_db()
        self.assertIsNone(refreshed.source_render_id)

    def test_source_privacy_validation_and_cancelled_copy(self):
        original = self.uploaded(b"test video")
        endpoint = reverse("create_alternate", args=[original.id])
        self.assertEqual(Client().post(endpoint, {}, content_type="application/json").status_code, 404)
        self.assertEqual(Client(enforce_csrf_checks=True).post(endpoint, {}, content_type="application/json").status_code, 403)
        self.assertEqual(self.alternate(original.id, studio_mode="shorts").status_code, 400)
        self.assertEqual(self.alternate(original.id, highlight_minutes=31).status_code, 400)
        self.assertEqual(Job.objects.count(), 1)

        result = self.alternate(original.id)
        derived = Job.objects.get(pk=result.json()["id"])

        def cancel_copy(*args, **kwargs):
            if kwargs.get("event") is False:
                raise RenderCancelled

        with self.assertRaises(RenderCancelled):
            stage_alternate_source(derived, job_directory(derived.id) / "work", cancel_copy)
        self.assertFalse(uploaded_source(derived.id).exists())
        self.assertEqual(list(uploaded_source(derived.id).parent.iterdir()), [])
        self.assertEqual(uploaded_source(original.id).read_bytes(), b"test video")
        uploaded_source(original.id).unlink()
        self.assertEqual(self.alternate(original.id).status_code, 409)
