import io
import json
import shutil
import subprocess
import tempfile
from contextlib import nullcontext
from pathlib import Path
from unittest import skipUnless
from unittest.mock import Mock, patch
from zipfile import ZipFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from shorts_generator.highlights import _sanitize_highlights, call_highlight_api, get_highlights
from shorts_generator.local.clipper import crop_clip_local
from shorts_generator.local.llm import RankingConfig
from shorts_generator.pipeline import generate_shorts

from .exports import publication_filename
from .models import Job, WorkspacePreferences
from .worker import process_next_job


class StudioTests(TestCase):
    data = {
        "source_url": "https://youtu.be/abcdefghijk?list=ignore",
        "clip_count": 1, "aspect_ratio": "9:16", "quality": "720",
    }

    def create(self, data=None, client=None, **headers):
        client = client or self.client
        return client.post(
            reverse("jobs"), data or self.data, content_type="application/json", **headers,
        )

    def test_canonical_url_and_browser_ownership(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        job = response.json()
        self.assertEqual(job["source_url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(self.client.get(reverse("jobs")).json()["jobs"][0]["id"], job["id"])
        other = Client()
        self.assertEqual(other.get(reverse("jobs")).json()["jobs"], [])
        for action in ("detail", "archive"):
            self.assertEqual(other.get(reverse(action, args=[job["id"]])).status_code, 404)

    def test_rejects_files_playlists_and_lookalike_hosts(self):
        for url in (
            "file:///tmp/source.mp4",
            "https://www.youtube.com.example.org/watch?v=abcdefghijk",
            "https://www.youtube.com/playlist?list=abcdefghijk",
        ):
            with self.subTest(url=url):
                response = self.create({**self.data, "source_url": url})
                self.assertEqual(response.status_code, 400)
                self.assertIn("source_url", response.json()["fields"])
        self.assertEqual(Job.objects.count(), 0)

    def test_csrf_token_is_required_for_submission(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(self.create(client=client).status_code, 403)
        token = client.get(reverse("session")).json()["csrf_token"]
        self.assertEqual(self.create(client=client, HTTP_X_CSRFTOKEN=token).status_code, 201)

    def test_failed_job_can_be_retried_without_exposing_raw_exception(self):
        job_id = self.create().json()["id"]
        with patch("studio.worker.generate_shorts", side_effect=RuntimeError("private error")):
            self.assertTrue(process_next_job())
        detail = self.client.get(reverse("detail", args=[job_id])).json()
        self.assertEqual(detail["status"], "failed")
        self.assertNotIn("private error", detail["error"])
        self.assertEqual(detail["events"][-1]["level"], "error")
        self.assertNotIn("private error", detail["events"][-1]["message"])
        url = reverse("retry", args=[job_id])
        retry = self.client.post(url).json()
        self.assertEqual(retry["status"], "queued")
        self.assertEqual(retry["events"][-1]["message"], "Retry queued")
        self.assertEqual(self.client.post(url).status_code, 409)

    def test_score_job_with_no_matches_finishes_without_an_archive(self):
        created = self.create({**self.data, "selection_mode": "score", "min_viral_score": 95}).json()
        with tempfile.TemporaryDirectory() as temp, override_settings(STUDIO_DATA_DIR=Path(temp)):
            with patch("studio.worker.generate_shorts", return_value=[]) as generate, patch("studio.worker.make_export") as export:
                self.assertTrue(process_next_job())
                self.assertEqual(generate.call_args.kwargs["min_viral_score"], 95)
                export.assert_not_called()
            result = self.client.get(reverse("detail", args=[created["id"]])).json()
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["clips"], [])
            self.assertIsNone(result["archive_url"])
            self.assertEqual(result["events"][-1]["message"], "No highlights scored 95 or higher")
            self.assertEqual(self.client.get(reverse("archive", args=[created["id"]])).status_code, 404)

    @skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools required")
    def test_worker_packages_playable_video_thumbnail_and_metadata(self):
        job_id = self.create().json()["id"]

        def generate(url, *, out_dir, on_progress, **options):
            source = Path(out_dir) / "source.mp4"
            video = Path(out_dir) / "short_01.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=24", "-f", "lavfi", "-i", "sine=frequency=440",
                    "-t", "4", "-c:v", "libx264", "-c:a", "aac", str(source),
                ], check=True,
            )
            on_progress("render", 70)
            thumbnail = video.with_suffix(".jpg")
            crop_clip_local(str(source), 0.5, 2.5, "9:16", str(video), thumbnail_path=thumbnail)
            captions = video.with_suffix(".srt")
            captions.write_text("1\n00:00:00,100 --> 00:00:01,500\nA clip about coffee.\n", encoding="utf-8")
            return [{
                "title": "Café / A story", "description": "A clip about coffee.",
                "start_time": 0.5, "end_time": 2.5, "score": 91, "clip_url": str(video),
                "caption_path": str(captions), "thumbnail_path": str(thumbnail),
            }]

        with tempfile.TemporaryDirectory() as temp, override_settings(STUDIO_DATA_DIR=Path(temp)):
            with patch("studio.worker.generate_shorts", side_effect=generate):
                self.assertTrue(process_next_job())
            job = self.client.get(reverse("detail", args=[job_id])).json()
            self.assertEqual(job["status"], "complete")
            self.assertEqual(job["progress"], 100)
            self.assertEqual(len(job["clips"]), 1)
            response = self.client.get(job["archive_url"])
            self.assertEqual(response.status_code, 200)
            with ZipFile(io.BytesIO(b"".join(response.streaming_content))) as archive:
                self.assertIsNone(archive.testzip())
                stem = "01/Café A story"
                self.assertEqual(set(archive.namelist()), {f"{stem}.{ext}" for ext in ("mp4", "jpg", "txt", "srt")})
                thumbnail_bytes = archive.read(f"{stem}.jpg")
                self.assertTrue(thumbnail_bytes.startswith(b"\xff\xd8"))
                self.assertEqual(archive.read(f"{stem}.txt").decode(), "Café / A story\n\nA clip about coffee.\n")
                video_path = Path(temp) / "clip.mp4"
                video_path.write_bytes(archive.read(f"{stem}.mp4"))
            probe = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video_path),
            ]))
            video, audio = probe["streams"]
            self.assertEqual(video["codec_name"], "h264")
            self.assertEqual(video["width"] * 16, video["height"] * 9)
            self.assertEqual(audio["codec_name"], "aac")
            self.assertAlmostEqual(float(probe["format"]["duration"]), 2.0, delta=0.1)
            self.assertEqual(probe["format"]["tags"]["title"], "Café / A story")

            video_url = job["clips"][0]["video_url"]
            partial = self.client.get(video_url, HTTP_RANGE="bytes=0-15")
            self.assertEqual(partial.status_code, 206)
            self.assertEqual(b"".join(partial.streaming_content), video_path.read_bytes()[:16])
            self.assertEqual(self.client.get(video_url, HTTP_RANGE="bytes=999999999-").status_code, 416)
            self.assertEqual(Client().get(video_url).status_code, 404)
            caption_url = job["clips"][0]["captions_url"]
            self.assertEqual(Client().get(caption_url).status_code, 404)
            caption_response = self.client.get(caption_url)
            caption_text = b"".join(caption_response.streaming_content)
            self.assertIn(b"A clip about coffee.", caption_text)
            metadata = {"title": "A better title", "description": "The corrected description."}
            edit_url = job["clips"][0]["edit_url"]
            self.assertEqual(Client().post(edit_url, metadata, content_type="application/json").status_code, 404)
            updated = self.client.post(edit_url, metadata, content_type="application/json")
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["clips"][0]["title"], metadata["title"])
            self.assertEqual(updated.json()["clips"][0]["video_url"], video_url)

            thumbnail_url = job["clips"][0]["thumbnail_url"]
            self.assertEqual(Client().get(thumbnail_url, {"download": 1}).status_code, 404)
            jpg = self.client.get(thumbnail_url, {"download": 1})
            self.assertEqual(jpg["Content-Disposition"], 'attachment; filename="A better title.jpg"')
            self.assertEqual(b"".join(jpg.streaming_content), thumbnail_bytes)
            self.assertEqual(Client().get(video_url, {"download": 1}).status_code, 404)
            mp4 = self.client.get(video_url, {"download": 1})
            self.assertEqual(mp4["Content-Disposition"], 'attachment; filename="A better title.mp4"')
            downloaded_video = b"".join(mp4.streaming_content)
            renamed = video_path.with_name("renamed.mp4")
            renamed.write_bytes(downloaded_video)
            tags = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "format_tags=title", "-of", "json", str(renamed),
            ]))
            self.assertEqual(tags["format"]["tags"]["title"], metadata["title"])
            def stream_hash(path):
                return subprocess.check_output([
                    "ffmpeg", "-v", "error", "-i", str(path), "-map", "0", "-c", "copy", "-f", "streamhash", "-",
                ])

            self.assertEqual(stream_hash(video_path), stream_hash(renamed))

            response = self.client.get(job["archive_url"])
            with ZipFile(io.BytesIO(b"".join(response.streaming_content))) as archive:
                stem = "01/A better title"
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.read(f"{stem}.mp4"), downloaded_video)
                self.assertEqual(archive.read(f"{stem}.jpg"), thumbnail_bytes)
                self.assertEqual(archive.read(f"{stem}.txt").decode(), "A better title\n\nThe corrected description.\n")
                self.assertEqual(archive.read(f"{stem}.srt"), caption_text)
            text = self.client.get(job["clips"][0]["text_url"])
            self.assertEqual(b"".join(text.streaming_content).decode(), "A better title\n\nThe corrected description.\n")


class WorkspacePreferencesTests(TestCase):
    data = {
        "clip_count": 3, "aspect_ratio": "1:1", "quality": "1080", "language": "ur",
        "selection_mode": "count", "min_viral_score": 80,
        "framing": "fit", "caption_style": "highlight",
        "min_clip_seconds": 15, "max_clip_seconds": 30,
        "theme": "dark", "show_scores": False,
    }

    def test_preferences_persist_for_the_browser_that_saved_them(self):
        path = reverse("preferences")
        defaults = self.client.get(path).json()["defaults"]
        self.assertEqual(WorkspacePreferences.objects.count(), 0)
        response = self.client.post(path, self.data, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(path).json()["preferences"], self.data)
        self.assertEqual(Client().get(path).json()["preferences"], defaults)

        response = self.client.post(path, defaults, content_type="application/json")
        self.assertEqual(response.json()["preferences"], defaults)
        self.assertEqual(WorkspacePreferences.objects.count(), 1)

    def test_validation_and_csrf_protect_saved_preferences(self):
        client = Client(enforce_csrf_checks=True)
        path = reverse("preferences")
        self.assertEqual(client.post(path, self.data, content_type="application/json").status_code, 403)
        token = client.get(reverse("session")).json()["csrf_token"]
        invalid = {**self.data, "clip_count": 11, "theme": "unknown", "show_scores": "unknown"}
        result = client.post(path, invalid, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(set(result.json()["fields"]), {"clip_count", "theme", "show_scores"})
        self.assertFalse(WorkspacePreferences.objects.exists())

    def test_new_job_uses_saved_defaults_and_accepts_per_render_overrides(self):
        path = reverse("preferences")
        self.client.post(path, self.data, content_type="application/json")
        response = self.client.post(
            reverse("jobs"), {"source_url": "https://youtu.be/abcdefghijk", "clip_count": 2},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        job = Job.objects.get()
        self.assertEqual((job.clip_count, job.aspect_ratio, job.quality, job.language), (2, "1:1", "1080", "ur"))
        self.assertEqual((job.framing, job.caption_style), ("fit", "highlight"))
        self.assertEqual((job.min_clip_seconds, job.max_clip_seconds), (15, 30))
        self.assertEqual(self.client.get(path).json()["preferences"], self.data)

    def test_maximum_clip_length_must_be_at_least_the_minimum(self):
        response = self.client.post(
            reverse("preferences"), {**self.data, "min_clip_seconds": 60, "max_clip_seconds": 30},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("max_clip_seconds", response.json()["fields"])

    def test_saved_score_selection_is_used_by_uploads_and_can_be_overridden(self):
        preferences_url = reverse("preferences")
        saved = {**self.data, "selection_mode": "score", "min_viral_score": 85}
        self.assertEqual(self.client.post(preferences_url, saved, content_type="application/json").status_code, 200)
        result = self.client.post(reverse("jobs"), {"source_url": "https://youtu.be/abcdefghijk"},
                                  content_type="application/json").json()
        self.assertEqual((result["selection_mode"], result["min_viral_score"]), ("score", 85))
        with tempfile.TemporaryDirectory() as temp, override_settings(STUDIO_DATA_DIR=Path(temp)):
            uploaded = self.client.post(reverse("upload_job"), {"video": SimpleUploadedFile("source.mp4", b"test")}).json()
        self.assertEqual((uploaded["selection_mode"], uploaded["min_viral_score"]), ("score", 85))
        result = self.client.post(reverse("jobs"), {"source_url": "https://youtu.be/abcdefghijk",
                                                   "selection_mode": "count", "clip_count": 2},
                                  content_type="application/json").json()
        self.assertEqual((result["selection_mode"], result["clip_count"]), ("count", 2))
        self.assertEqual(self.client.get(preferences_url).json()["preferences"], saved)
        for threshold in (None, -1, 101, 80.5):
            response = self.client.post(preferences_url, {"min_viral_score": threshold}, content_type="application/json")
            self.assertEqual(response.status_code, 400)
            self.assertIn("min_viral_score", response.json()["fields"])


class HighlightTests(SimpleTestCase):
    def test_publication_filename_keeps_title_case_and_handles_filesystem_limits(self):
        self.assertEqual(publication_filename("How I Beat Level 5!"), "How I Beat Level 5!")
        self.assertEqual(publication_filename("Café / A story?"), "Café A story")
        self.assertEqual(publication_filename("CON.txt"), "_CON.txt")
        self.assertEqual(publication_filename('...'), "Clip")
        self.assertLessEqual(len(publication_filename("🦊" * 100).encode("utf-8")), 180)

    def test_score_selection_includes_the_cutoff_and_has_no_count_cap(self):
        transcript = {"duration": 36, "segments": [
            {"start": 0, "end": 6, "text": "The first story."},
            {"start": 12, "end": 18, "text": "The second story."},
            {"start": 24, "end": 30, "text": "The third story."},
        ]}
        candidates = json.dumps({"highlights": [
            {"start_time": 0, "end_time": 6, "score": 79},
            {"start_time": 12, "end_time": 18, "score": 80},
            {"start_time": 24, "end_time": 30, "score": 91},
        ]})
        ranking = RankingConfig(provider="compatible", model="fixture", base_url="http://localhost:1234/v1")
        for threshold, expected in ((80, [91, 80]), (None, [91]), (95, [])):
            with self.subTest(threshold=threshold):
                rank = Mock(side_effect=["{}", candidates])
                with (
                    patch("shorts_generator.pipeline.which", return_value="ffmpeg"),
                    patch("shorts_generator.pipeline.download_youtube_local", return_value="/source.mp4"),
                    patch("shorts_generator.pipeline.transcribe_local", return_value=transcript),
                    patch("shorts_generator.pipeline.ranking_client", return_value=nullcontext(rank)),
                    patch("shorts_generator.pipeline.crop_highlights_local", side_effect=lambda _, clips, **kw: clips) as crop,
                ):
                    result = generate_shorts("https://youtu.be/abcdefghijk", out_dir="/work",
                                             ranking=ranking, num_clips=1, min_viral_score=threshold)
                    self.assertEqual([clip["score"] for clip in result], expected)
                    self.assertEqual(crop.called, bool(expected))

    def test_score_mode_accepts_an_empty_model_response_without_retrying(self):
        rank = Mock(return_value='{"highlights":[]}')
        result = call_highlight_api("Short story.", {}, 36, num_clips=1, llm_fn=rank, min_viral_score=80)
        self.assertEqual(result["highlights"], [])
        rank.assert_called_once()
        self.assertIn("score of 80 or higher", rank.call_args.args[0])

    def test_requested_duration_caps_or_extends_model_spans(self):
        transcript = {"duration": 34, "segments": [
            {"start": 1, "end": 3, "text": "Payoff opening."},
            {"start": 5, "end": 8, "text": "Payoff details."},
            {"start": 12, "end": 14, "text": "Second opening."},
            {"start": 22, "end": 32, "text": "Extra material."},
        ]}
        replies = iter(["{}", json.dumps({"highlights": [
            {"start_time": 1, "end_time": 3, "score": 88},
            {"start_time": 12, "end_time": 32, "score": 79},
        ]})])
        result = get_highlights(transcript, llm_fn=lambda _: next(replies), clip_length=(6, 9))
        self.assertEqual(len(result["highlights"]), 2)
        self.assertEqual([(clip["start_time"], clip["end_time"]) for clip in result["highlights"]], [(1, 8), (12, 21)])

    def test_publication_copy_uses_the_ranking_request_and_speech_fallback(self):
        transcript = {"duration": 19, "segments": [
            {"start": 0, "end": 3, "text": "Unrelated introduction."},
            {"start": 4, "end": 7, "text": "コーヒーを冷やします。香りを長く保てます。"},
            {"start": 10, "end": 15, "text": "Let the water cool. Stop the kettle at 90 degrees."},
        ]}
        prompts = []

        def rank(prompt):
            prompts.append(prompt)
            if len(prompts) == 1:
                return "{}"
            return json.dumps({"highlights": [
                {"start_time": 4, "end_time": 7, "score": 88, "title": "Untitled Highlight", "description": 7},
                {"start_time": 10, "end_time": 15, "score": 79, "title": "**Title:** Cool the kettle before brewing",
                 "description": "Description: Cool the kettle before brewing."},
            ]})

        first, second = get_highlights(transcript, llm_fn=rank)["highlights"]
        self.assertEqual(len(prompts), 2)
        self.assertIn("concrete payoff", prompts[-1])
        self.assertEqual(first["title"], "コーヒーを冷やします")
        self.assertEqual(first["description"], "香りを長く保てます。")
        self.assertEqual(second["title"], "Cool the kettle before brewing")
        self.assertEqual(second["description"], "Let the water cool. Stop the kettle at 90 degrees.")

    def test_chunk_timestamps_are_shifted_only_once(self):
        transcript = {
            "duration": 1850,
            "segments": [
                {"start": 15, "end": 19, "text": "First moment"},
                {"start": 1205, "end": 1215, "text": "Second moment"},
            ],
        }
        calls = []

        def rank(text, *args, **kwargs):
            calls.append(text)
            start = 15 if "First" in text else 65
            return {"highlights": [{"start_time": start, "end_time": start + 5, "score": 80}]}

        with patch("shorts_generator.highlights.call_highlight_api", side_effect=rank):
            result = get_highlights(transcript, llm_fn=lambda _: "{}")
        self.assertIn("[65.0–75.0s] Second moment", calls[1])
        self.assertEqual([clip["start_time"] for clip in result["highlights"]], [15, 1205])

    def test_invalid_timestamps_are_dropped(self):
        self.assertEqual(_sanitize_highlights([
            {"start_time": "NaN", "end_time": 10},
            {"start_time": 2, "end_time": "Infinity"},
        ], 12), [])
