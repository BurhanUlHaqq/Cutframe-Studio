import io
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from shorts_generator.pipeline import NoMatchingHighlights
from shorts_generator.reel import Moment, choose_moments, generate_highlight_reel, transcript_moments

from .models import Job
from .worker import process_next_job


class ReelPlannerTests(SimpleTestCase):
    def test_model_beats_are_grounded_and_selected_in_source_order(self):
        segments = [
            {"start": 7, "end": 30, "text": "Introduce the challenge and set up the first attempt."},
            {"start": 605, "end": 640, "text": "The final attempt succeeds and the result is shown."},
        ]
        responses = iter([
            '{"excluded_ranges":[],"moments":[{"start_time":8,"end_time":31,"title":"First attempt","score":80}]}',
            '{"excluded_ranges":[],"moments":[{"start_time":5,"end_time":41,"title":"Final attempt","score":100},'
            '{"start_time":800,"end_time":850,"title":"Invented scene","score":100}]}',
        ])
        candidates = transcript_moments(segments, 700, 240, lambda _: next(responses), lambda *_: None)
        selected = choose_moments(candidates, 700, 90)
        self.assertEqual([(moment.start, moment.end) for moment in selected], [(7, 30), (605, 640)])

    def test_exclusions_cover_whole_utterances_in_overlapping_sections(self):
        segments = [
            {"start": 580, "end": 594, "text": "The setup."},
            {"start": 594, "end": 609, "text": "Content requiring exclusion."},
            {"start": 610, "end": 640, "text": "A separate safe topic."},
        ]
        responses = iter([
            '{"excluded_ranges":[],"moments":[{"start_time":580,"end_time":600,"title":"Setup","score":100}]}',
            '{"excluded_ranges":[{"start_time":2,"end_time":5}],"moments":'
            '[{"start_time":10,"end_time":40,"title":"Safe topic","score":70}]}',
        ])
        candidates = transcript_moments(segments, 700, 240, lambda _: next(responses), lambda *_: None)
        self.assertEqual([(moment.start, moment.end) for moment in candidates], [(610, 640)])

    def test_short_source_can_pass_transcript_screening(self):
        segments = [{"start": 0, "end": 7, "text": "A complete short demonstration."}]
        response = '{"excluded_ranges":[],"moments":[{"start_time":0,"end_time":7,"title":"Demo","score":90}]}'
        candidates = transcript_moments(segments, 7, 7, lambda _: response, lambda *_: None)
        self.assertEqual(choose_moments(candidates, 7, 7), [Moment(0, 7, "Demo", 90)])

    def test_model_rejection_never_falls_back_to_motion(self):
        segments = [{"start": 0, "end": 16, "text": "A spoken passage."}]
        responses = [
            '{"moments":[{"start_time":0,"end_time":16,"title":"Missing screening","score":100}]}',
            '{"excluded_ranges":[{"start_time":0,"end_time":16}],"moments":[{"start_time":0,'
            '"end_time":16,"title":"Excluded despite high score","score":100}]}',
        ]
        for response in responses:
            with (self.subTest(response=response),
                  patch("shorts_generator.reel.which", return_value="ffmpeg"),
                  patch("shorts_generator.reel.prepare_source", return_value="source.mp4"),
                  patch("shorts_generator.reel.validate_local_video", return_value=(30, True, {})),
                  patch("shorts_generator.reel.transcribe_source", return_value={"segments": segments}),
                  patch("shorts_generator.reel.ranking_client") as client,
                  patch("shorts_generator.reel.activity_moments") as activity):
                ranking = client.return_value
                ranking.provider = "openai"
                client.return_value.__enter__.return_value.return_value = response
                with self.assertRaisesMessage(NoMatchingHighlights, "No usable moments passed transcript screening"):
                    generate_highlight_reel("", out_dir=".", minutes=1, method="transcript", ranking=ranking)
                activity.assert_not_called()

    def test_selection_respects_thirty_minute_budget_and_removes_duplicate_beats(self):
        moments = [Moment(index * 300, index * 300 + 180, f"Topic {index}", 90) for index in range(16)]
        moments += [Moment(0, 170, "Overlapping scene", 85)]
        selected = choose_moments(moments, 4800, 1800)
        self.assertEqual(sum(moment.duration for moment in selected), 1800)
        self.assertEqual(len(selected), 10)
        self.assertTrue(all(left.end <= right.start for left, right in zip(selected, selected[1:])))


class HighlightStudioTests(TestCase):
    def test_mode_defaults_persist_alongside_short_clip_options(self):
        defaults = self.client.post(reverse("preferences"), {
            "studio_mode": "highlights", "highlight_minutes": 30, "highlight_method": "activity",
            "aspect_ratio": "1:1", "clip_count": 7,
        }, content_type="application/json")
        self.assertEqual(defaults.status_code, 200)
        urls = {"source_url": "https://youtu.be/abcdefghijk"}
        reel = self.client.post(reverse("jobs"), urls, content_type="application/json").json()
        self.assertEqual((reel["studio_mode"], reel["highlight_minutes"], reel["highlight_method"],
                          reel["aspect_ratio"], reel["clip_count"]), ("highlights", 30, "activity", "16:9", 1))
        shorts = self.client.post(reverse("jobs"), {**urls, "studio_mode": "shorts"},
                                  content_type="application/json").json()
        self.assertEqual((shorts["aspect_ratio"], shorts["clip_count"]), ("1:1", 7))
        invalid = self.client.post(reverse("jobs"), {**urls, "highlight_minutes": 31}, content_type="application/json")
        self.assertIn("highlight_minutes", invalid.json()["fields"])

    def test_uploaded_video_renders_one_landscape_reel_with_a_chaptered_zip(self):
        with TemporaryDirectory() as temporary, override_settings(STUDIO_DATA_DIR=Path(temporary)):
            source = Path(temporary) / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=192x108:r=12",
                            "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=48000",
                            "-t", "96", "-c:v", "libx264", "-preset", "ultrafast", str(source)],
                           check=True, capture_output=True)
            upload = self.client.post(reverse("upload_job"), {
                "video": SimpleUploadedFile("Weekend match.mp4", source.read_bytes()),
                "studio_mode": "highlights", "highlight_minutes": 1, "highlight_method": "activity",
                "quality": "480", "caption_style": "classic",
            })
            self.assertEqual(upload.status_code, 201, upload.content)
            segments = [{"start": time + 0.5, "end": time + 2, "text": f"Play {time // 3}."}
                        for time in range(0, 96, 3)]
            with patch("shorts_generator.reel.transcribe_source", return_value={"segments": segments}):
                self.assertTrue(process_next_job())
            job = Job.objects.get()
            self.assertEqual(job.status, Job.Status.COMPLETE, job.error)
            self.assertEqual(len(job.clips), 1)
            reel = job.clips[0]
            self.assertEqual(reel["title"], "Weekend match | Highlights")
            self.assertEqual(reel["highlight_review"], "activity")
            self.assertLessEqual(reel["end_time"], 60)
            self.assertGreaterEqual(len(reel["chapters"]), 3)
            self.assertTrue(reel["chapter_text"].startswith("00:00 "), reel["chapters"])
            self.assertEqual(reel["chapters"], sorted(reel["chapters"], key=lambda chapter: chapter["source_time"]))

            url = self.client.get(reverse("archive", args=[job.id]))
            with ZipFile(io.BytesIO(b"".join(url.streaming_content))) as archive:
                self.assertIsNone(archive.testzip())
                names = {Path(name).suffix: name for name in archive.namelist()}
                self.assertEqual(set(names), {".mp4", ".jpg", ".txt", ".srt"})
                self.assertIn(reel["chapter_text"], archive.read(names[".txt"]).decode())
                self.assertIn("-->", archive.read(names[".srt"]).decode())
                output = Path(temporary) / "exported.mp4"
                output.write_bytes(archive.read(names[".mp4"]))
            probe = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output),
            ]))
            video, audio = probe["streams"]
            self.assertEqual((video["width"], video["height"], audio["codec_name"]), (854, 480, "aac"))
            self.assertAlmostEqual(float(probe["format"]["duration"]), reel["end_time"], delta=0.1)
