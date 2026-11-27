import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from shorts_generator.local.media import validate_local_video

from .exports import job_directory
from .models import Job
from .worker import process_next_job


class TimestampRenderTests(TestCase):
    def test_timestamp_validation_and_alternate_defaults(self):
        source = "https://youtu.be/YE7VzlLtp-4"
        for fields, error in [
            ({"source_start": "0"}, "source_end"),
            ({"source_end": "30"}, "source_start"),
            ({"source_start": "1:02.5", "source_end": "62.5"}, "source_end"),
            ({"source_start": "1:60", "source_end": "80"}, "source_start"),
        ]:
            response = self.client.post(reverse("jobs"), {"source_url": source, **fields}, content_type="application/json")
            self.assertEqual(response.status_code, 400)
            self.assertIn(error, response.json()["fields"])
        response = self.client.post(reverse("jobs"), {
            "source_url": source, "source_start": "1:10:00.250", "source_end": "4210.5",
            "selection_mode": "score", "clip_count": 5,
        }, content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        job = response.json()
        self.assertEqual((job["source_start_ms"], job["source_end_ms"]), (4200250, 4210500))
        self.assertEqual((job["clip_count"], job["selection_mode"]), (1, "count"))
        original = Job.objects.get(pk=job["id"])
        original.status = Job.Status.COMPLETE
        original.save(update_fields=["status"])
        endpoint = reverse("create_alternate", args=[original.pk])
        inherited = self.client.post(endpoint, {}, content_type="application/json")
        self.assertEqual(inherited.status_code, 201, inherited.content)
        self.assertEqual(inherited.json()["source_start_ms"], 4200250)
        cleared = self.client.post(endpoint, {"source_start": "", "source_end": ""}, content_type="application/json")
        self.assertEqual(cleared.status_code, 201, cleared.content)
        self.assertIsNone(cleared.json()["source_start_ms"])

    def test_exact_clip_uses_selected_picture_and_transcribes_only_that_range(self):
        with TemporaryDirectory() as temporary, override_settings(STUDIO_DATA_DIR=Path(temporary)):
            source = Path(temporary) / "source.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=160x90:r=12:d=4",
                "-f", "lavfi", "-i", "color=lime:s=160x90:r=12:d=4",
                "-f", "lavfi", "-i", "sine=frequency=400:sample_rate=48000",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[video]", "-map", "[video]", "-map", "2:a",
                "-t", "8", "-c:v", "libx264", "-preset", "ultrafast", str(source),
            ], check=True, capture_output=True)
            response = self.client.post(reverse("upload_job"), {
                "video": SimpleUploadedFile("Fixture.mp4", source.read_bytes()), "source_start": "0:04.250",
                "source_end": "6.5", "aspect_ratio": "16:9", "editing_style": "fast", "caption_style": "classic",
            })
            self.assertEqual(response.status_code, 201, response.content)

            def transcript(path, *_args, **_kwargs):
                duration, _, _ = validate_local_video(Path(path))
                self.assertAlmostEqual(duration, 2.25, delta=0.1)
                return {"segments": [{"start": 0.2, "end": 1.0, "text": "Selected speech."}], "duration": duration}

            with (patch("shorts_generator.pipeline.transcribe_source", side_effect=transcript) as transcribe,
                  patch("shorts_generator.pipeline.ranking_client") as rank):
                self.assertTrue(process_next_job())
            transcribe.assert_called_once()
            rank.assert_not_called()
            job = Job.objects.get(pk=response.json()["id"])
            self.assertEqual(job.status, Job.Status.COMPLETE, job.error)
            self.assertEqual(len(job.clips), 1)
            clip = job.clips[0]
            self.assertEqual((clip["start_time"], clip["end_time"], clip["score"]), (4.25, 6.5, None))
            self.assertTrue(clip["has_captions"])
            self.assertNotIn("pauses_trimmed", clip)
            result = job_directory(job.id) / "files" / "clip-01.mp4"
            self.assertAlmostEqual(validate_local_video(result)[0], 2.25, delta=0.15)
            pixel = subprocess.check_output([
                "ffmpeg", "-v", "error", "-i", str(result), "-frames:v", "1",
                "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
            ])
            self.assertGreater(pixel[1], max(pixel[0], pixel[2]) + 100)


    def test_highlight_chapters_keep_original_source_timestamps(self):
        with TemporaryDirectory() as temporary, override_settings(STUDIO_DATA_DIR=Path(temporary)):
            source = Path(temporary) / "source.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=26",
                "-c:v", "libx264", "-preset", "ultrafast", str(source),
            ], check=True, capture_output=True)
            response = self.client.post(reverse("upload_job"), {
                "video": SimpleUploadedFile("Highlights.mp4", source.read_bytes()),
                "source_start": "0:09", "source_end": "22", "studio_mode": "highlights",
                "highlight_method": "activity", "highlight_minutes": 1, "quality": "480", "caption_style": "off",
            })
            self.assertEqual(response.status_code, 201, response.content)
            self.assertTrue(process_next_job())
            job = Job.objects.get(pk=response.json()["id"])
            self.assertEqual(job.status, Job.Status.COMPLETE, job.error)
            reel = job.clips[0]
            self.assertLessEqual(reel["end_time"], 13.1)
            self.assertTrue(reel["chapters"])
            self.assertTrue(all(9 <= chapter["source_time"] < 22 for chapter in reel["chapters"]))
            self.assertEqual(reel["chapters"][0]["time"], 0)
