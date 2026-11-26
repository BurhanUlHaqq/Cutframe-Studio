import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .checkpoints import load_render_checkpoint
from .exports import job_directory
from .models import Job, WorkspaceThumbnailSettings
from .thumbnail_ai import ThumbnailGenerationError
from .worker import process_next_job, run_queue


class RenderControlTests(TestCase):
    def setUp(self):
        self.client.get(reverse("session"))
        self.owner = self.client.session["studio_owner"]
        self.storage = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(override_settings(STUDIO_DATA_DIR=self.storage))

    def render(self, **options):
        return Job.objects.create(**({"owner": self.owner, "source_name": "Video.mp4", "clip_count": 3,
                                      "aspect_ratio": "9:16", "quality": "720", "editing_style": "gameplay"} | options))

    def action(self, name, job):
        return self.client.post(reverse(name, args=[job.id]), {}, content_type="application/json")

    def sample_assets(self):
        video = self.storage / "video.mp4"
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=180x320:r=15:d=0.3",
            "-c:v", "libx264", str(video),
        ], check=True, capture_output=True)
        image = self.storage / "cover.jpg"
        Image.new("RGB", (180, 320), "green").save(image)
        return video, image

    def test_queued_cancel_is_private_and_run_again_preserves_the_source(self):
        job = self.render()
        upload = job_directory(job.id) / "input" / "source"
        upload.parent.mkdir(parents=True)
        upload.write_bytes(b"original")
        self.assertEqual(Client().post(reverse("cancel_render", args=[job.id])).status_code, 404)
        self.assertEqual(self.action("cancel_render", job).json()["status"], "cancelled")
        self.assertEqual(self.action("cancel_render", job).json()["status"], "cancelled")
        self.assertFalse(process_next_job())
        self.assertEqual(upload.read_bytes(), b"original")
        self.assertEqual(self.action("retry", job).json()["status"], "queued")

    def test_active_cancel_stops_before_publishing_rendered_media(self):
        job = self.render()

        def generate(*args, on_progress, **kwargs):
            on_progress("rank", 55, "Selecting videos")
            self.assertEqual(self.action("cancel_render", job).json()["status"], "cancelling")
            on_progress("render", 70, "Rendering first video")
            self.fail("Cancellation should stop the pipeline at the following checkpoint")

        with patch("studio.worker.generate_shorts", side_effect=generate), patch("studio.worker.make_export") as export:
            self.assertTrue(process_next_job())
        export.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.CANCELLED)
        self.assertIn("Stopping after the current operation.", [entry["message"] for entry in job.events])
        self.assertIsNone(self.client.get(reverse("detail", args=[job.id])).json()["archive_url"])

    def test_interrupted_cancellation_is_finalized_when_the_worker_restarts(self):
        job = self.render(status=Job.Status.CANCELLING)
        run_queue(once=True)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.CANCELLED)

    def test_cover_budget_skip_and_provider_quota_all_finish_the_zip(self):
        video, image = self.sample_assets()
        settings = {"provider": "compatible", "model": "fixture", "base_url": "http://localhost:7860/v1",
                    "quality": "low", "automatic": True}
        for mode in ("limit", "skip", "quota"):
            with self.subTest(mode=mode):
                WorkspaceThumbnailSettings.objects.update_or_create(owner=self.owner, defaults={
                    "config": {**settings, "automatic_limit": 1 if mode == "limit" else None},
                })
                job = self.render()
                work = job_directory(job.id) / "work"
                work.mkdir(parents=True)
                source = shutil.copyfile(video, work / "source.mp4")
                cover = shutil.copyfile(image, work / "source.jpg")
                clips = [{"clip_url": source, "thumbnail_path": cover, "title": f"Fixture {index}",
                          "start_time": 0, "end_time": .3, "score": 80} for index in range(3)]

                def generate_cover(*args, **kwargs):
                    if mode == "skip":
                        self.assertTrue(self.action("skip_ai_covers", job).json()["skip_ai_covers"])
                    if mode == "quota":
                        raise ThumbnailGenerationError("Quota exceeded.", stop_batch=True)
                    return image.read_bytes()

                with patch("studio.worker.generate_shorts", return_value=clips), \
                        patch("studio.worker.make_ai_cover", side_effect=generate_cover) as provider:
                    self.assertTrue(process_next_job())
                provider.assert_called_once()
                job.refresh_from_db()
                self.assertEqual(job.status, Job.Status.COMPLETE, job.error)
                self.assertEqual(sum(clip.get("thumbnail_source") == "ai" for clip in job.clips), 0 if mode == "quota" else 1)
                self.assertTrue(any(event["message"].startswith("Writing ZIP") for event in job.events))
                self.assertEqual(len(list(job_directory(job.id).glob("*.zip"))), 1)

    def test_retry_resumes_export_after_zip_failure(self):
        video, image = self.sample_assets()
        WorkspaceThumbnailSettings.objects.create(owner=self.owner, config={
            "provider": "compatible", "model": "fixture", "base_url": "http://localhost:7860/v1",
            "quality": "low", "automatic": True,
        })
        job = self.render()
        work = job_directory(job.id) / "work"
        work.mkdir(parents=True)
        source = shutil.copyfile(video, work / "source.mp4")
        cover = shutil.copyfile(image, work / "source.jpg")
        clips = [{"clip_url": source, "thumbnail_path": cover, "title": "Recovered clip",
                  "start_time": 0, "end_time": .3, "score": 80}]

        with patch("studio.worker.generate_shorts", return_value=clips) as generate, \
                patch("studio.worker.make_ai_cover", return_value=image.read_bytes()) as provider:
            with patch("studio.exports.open_archive", side_effect=OSError("fixture interrupted ZIP")):
                self.assertTrue(process_next_job())
            job.refresh_from_db()
            self.assertEqual(job.status, Job.Status.FAILED)
            self.assertEqual(job.stage, "export")
            self.assertIn("could not package", job.error)
            self.assertEqual(self.action("retry", job).json()["status"], "queued")
            self.assertTrue(process_next_job())
        generate.assert_called_once()
        provider.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.COMPLETE, job.error)
        self.assertEqual(job.clips[0]["thumbnail_source"], "ai")
        messages = [event["message"] for event in job.events]
        self.assertIn("Resuming export with 1 completed video", messages)
        self.assertIn("Reusing AI cover 1 of 1", messages)
        self.assertEqual(len(list(job_directory(job.id).glob("*.zip"))), 1)
        source.unlink()
        self.assertIsNone(load_render_checkpoint(work))
