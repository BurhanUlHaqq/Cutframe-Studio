from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from publishing.models import Platform, Publication

from .exports import job_directory, render_lock
from .models import Job, StudioMode


class RenderLibraryTests(TestCase):
    def setUp(self):
        self.client.get(reverse("session"))
        self.owner = self.client.session["studio_owner"]
        self.storage = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(STUDIO_DATA_DIR=Path(self.storage)))
        self.list_url = reverse("render_library")
        self.delete_url = reverse("delete_renders")

    def render(self, source, status=Job.Status.COMPLETE, **data):
        return Job.objects.create(**({"owner": self.owner, "source_name": source, "clip_count": 1,
                                      "status": status, "quality": "720", "aspect_ratio": "9:16"} | data))

    def write(self, job, name, size):
        path = job_directory(job.id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"a" * size)
        return path

    def publish(self, job, status):
        return Publication.objects.create(
            job=job, owner=job.owner, status=status, platform=Platform.YOUTUBE, account_id="channel",
            account_label="Channel", scheduled_at=timezone.now(), next_at=timezone.now(), clip_index=0,
            options={"title": "Game point", "caption": "The final rally."},
            url="https://www.youtube.com/watch?v=GamePoint",
        )

    def remove(self, *jobs):
        result = self.client.post(self.delete_url, {"ids": [str(job.id) for job in jobs]}, content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()

    def test_library_searches_old_clip_titles_and_sorts_render_sizes_across_pages(self):
        clips = [{"name": "clip-01", "title": "Opening"}, {"name": "clip-02", "title": "Décisive Rally"}]
        old = self.render("tournament.mp4", clips=clips)
        Job.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=91))
        self.write(old, "input/source", 800)
        self.write(old, "files/video.mp4", 150)
        self.write(old, "export.zip", 50)
        self.render("other workspace.mp4", owner=uuid4(), clips=clips)
        self.render("recent highlights.mov", studio_mode=StudioMode.HIGHLIGHTS)
        for index in range(14):
            self.render(f"new render {index}.mp4")
        result = self.client.get(self.list_url, {"q": "DÉCISIVE", "age": "90", "mode": "shorts", "status": "complete"}).json()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["renders"][0]["id"], str(old.id))
        self.assertEqual(result["renders"][0]["bytes"], 1000)
        self.assertTrue(result["renders"][0]["thumbnail_url"])
        result = self.client.get(self.list_url, {"sort": "largest"}).json()
        self.assertEqual(result["renders"][0]["id"], str(old.id))
        self.assertEqual(result["total"], 16)
        self.assertEqual(self.client.get(self.list_url, {"page": "2"}).json()["renders"][-1]["id"], str(old.id))
        self.assertEqual(Client().get(self.list_url).json()["total"], 0)

    def test_batch_deletes_only_owned_inactive_renders_and_keeps_published_records(self):
        old = self.render("old.mp4")
        running = self.render("encoding.mp4", Job.Status.RUNNING)
        scheduled = self.render("scheduled.mp4")
        foreign = self.render("foreign.mp4", owner=uuid4())
        for job in (old, running, scheduled, foreign):
            self.write(job, "input/source", 10)
            self.write(job, "export.zip", 15)
        completed = self.publish(old, Publication.Status.COMPLETE)
        self.publish(scheduled, Publication.Status.QUEUED)
        result = self.remove(old, running, scheduled, foreign)
        self.assertEqual(result["removed"], [{"id": str(old.id), "bytes_freed": 25}])
        self.assertEqual(len(result["blocked"]), 3)
        self.assertFalse(job_directory(old.id).exists())
        self.assertFalse(Job.objects.filter(pk=old.id).exists())
        self.assertTrue(all(job_directory(job.id).is_dir() for job in (running, scheduled, foreign)))
        self.assertEqual(self.client.get(reverse("detail", args=[old.id])).status_code, 404)
        completed.refresh_from_db()
        self.assertIsNone(completed.job_id)
        self.assertEqual(completed.status, Publication.Status.COMPLETE)
        self.assertTrue(completed.url)
        self.assertEqual(self.client.post(f"/api/publishing/posts/{completed.id}/thumbnail/", {},
                                          content_type="application/json").status_code, 409)

    def test_busy_or_partially_deleted_renders_remain_available_for_cleanup_retry(self):
        old = self.render("locked.mp4", Job.Status.FAILED)
        path = self.write(old, "work/clip.mp4", 40)
        with render_lock(old.id):
            result = self.remove(old)
            self.assertIn("in use", result["blocked"][0]["reason"])
            self.assertTrue(path.is_file())
        with patch("studio.library.shutil.rmtree", side_effect=PermissionError):
            self.assertEqual(self.remove(old)["removed"], [])
        old.refresh_from_db()
        self.assertEqual(old.status, Job.Status.DELETING)
        self.assertEqual(self.client.get(self.list_url, {"status": "deleting"}).json()["total"], 1)
        self.assertEqual(self.client.post(reverse("retry", args=[old.id]), {}, content_type="application/json").status_code, 409)
        self.assertEqual(self.remove(old)["removed"][0]["bytes_freed"], 40)
        self.assertFalse(path.exists())

    def test_work_cleanup_keeps_exports_and_pending_publications(self):
        ready = self.render("finished.mp4")
        pending = self.publish(ready, Publication.Status.QUEUED)
        work = self.write(ready, "work/source.mp4", 200)
        upload = self.write(ready, "input/source", 20)
        export = self.write(ready, "files/clip-01.mp4", 40)
        archive = self.write(ready, "clips-fixture.zip", 10)
        running = self.render("running.mp4", Job.Status.RUNNING)
        active_source = self.write(running, "work/source.mp4", 55)
        listed = self.client.get(self.list_url).json()["renders"]
        self.assertEqual(next(row for row in listed if row["id"] == str(ready.id))["work_bytes"], 200)
        response = self.client.post(reverse("clear_render_work"), {"ids": [str(ready.id), str(running.id)]},
                                    content_type="application/json")
        self.assertEqual(response.json()["removed"], [{"id": str(ready.id), "bytes_freed": 200}])
        self.assertEqual(response.json()["blocked"][0]["id"], str(running.id))
        self.assertFalse(work.exists())
        self.assertTrue(all(path.exists() for path in (upload, export, archive, active_source)))
        self.assertTrue(Job.objects.filter(pk=ready.id, status=Job.Status.COMPLETE).exists())
        self.assertTrue(Publication.objects.filter(pk=pending.id, job_id=ready.id, status=Publication.Status.QUEUED).exists())
