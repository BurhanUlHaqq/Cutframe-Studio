import io
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import ZipFile

from PIL import Image
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from shorts_generator.local.clipper import crop_highlights_local

from .exports import job_directory, make_export
from .models import Job


class ThumbnailEditorTests(TestCase):
    def test_candidates_preview_saved_jpg_zip_and_hashtags_use_the_same_render(self):
        with TemporaryDirectory() as temporary, override_settings(STUDIO_DATA_DIR=Path(temporary)):
            session = self.client.session
            session["studio_owner"] = str(uuid4())
            session.save()
            job = Job.objects.create(owner=session["studio_owner"], source_name="Skills.mp4", quality="720",
                                     clip_count=1, aspect_ratio="16:9")
            work = job_directory(job.id) / "work"
            work.mkdir(parents=True)
            source = work / "source.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=15:d=3",
                "-f", "lavfi", "-i", "smptebars=s=320x180:r=15:d=3",
                "-filter_complex", "[1:v]negate[b];[0:v][b]concat=n=2:v=1:a=0",
                "-c:v", "libx264", str(source),
            ], check=True, capture_output=True)
            shorts = crop_highlights_local(str(source), [{
                "title": "The Last Attempt", "cover_headline": "One Try Left", "score": 90,
                "description": "The final jump decides the result.",
                "hashtags": ["Skateboarding", "#SkateBoarding", "Viral", "FYP", "Street Skating", "技術"],
                "start_time": 0, "end_time": 6,
            }], out_dir=str(work), aspect_ratio="16:9", framing="center")
            job.clips = make_export(job, shorts)
            job.status = Job.Status.COMPLETE
            job.save(update_fields=["clips", "status"])
            result = self.client.get(reverse("detail", args=[job.id])).json()["clips"][0]
            self.assertEqual(result["thumbnail_design"]["headline"], "One Try Left")
            self.assertGreaterEqual(len(result["thumbnail_options"]), 2)
            self.assertEqual(result["description"],
                             "The final jump decides the result.\n\n#Skateboarding #StreetSkating #技術")
            choice = result["thumbnail_options"][1]
            self.assertEqual(Client().get(choice["url"]).status_code, 404)
            self.assertEqual(Client().post(result["thumbnail_save_url"], {}, content_type="application/json").status_code, 404)
            form = {"frame": 1, "headline": "The Final Jump", "accent": "mint", "placement": "top"}
            preview = self.client.post(result["thumbnail_preview_url"], form, content_type="application/json")
            self.assertEqual(preview.status_code, 200, preview.content)
            self.assertEqual(Image.open(io.BytesIO(preview.content)).size, (1920, 1080))
            self.assertIsNone(Job.objects.get(pk=job.pk).clips[0].get("thumbnail_file"))
            response = self.client.post(result["thumbnail_save_url"], form, content_type="application/json")
            self.assertEqual(response.status_code, 200, response.content)
            cover = self.client.get(response.json()["clips"][0]["thumbnail_url"])
            self.assertEqual(b"".join(cover.streaming_content), preview.content)

            archive = self.client.get(response.json()["archive_url"])
            with ZipFile(io.BytesIO(b"".join(archive.streaming_content))) as zipped:
                self.assertEqual(zipped.read("01/The Last Attempt.jpg"), preview.content)
                self.assertIn(result["description"], zipped.read("01/The Last Attempt.txt").decode("utf-8"))
