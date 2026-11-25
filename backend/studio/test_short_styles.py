import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from shorts_generator.local.clipper import crop_highlights_local
from shorts_generator.pipeline import generate_shorts


class ShortEditingStyleTests(SimpleTestCase):
    def test_pause_removal_keeps_audio_and_captions_on_the_edited_timeline(self):
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "speech.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=8",
                "-f", "lavfi", "-i", r"aevalsrc=if(between(t\,0.3\,1.1)+between(t\,3.3\,4.5)+"
                r"between(t\,6.6\,7.5)\,0.2*sin(2*PI*440*t)\,0):s=48000:d=8",
                "-c:v", "libx264", "-c:a", "aac", str(source),
            ], check=True, capture_output=True)
            segments = [{"start": .3, "end": 1.1, "text": "Opening."},
                        {"start": 3.3, "end": 4.5, "text": "Second sentence."},
                        {"start": 6.6, "end": 7.5, "text": "Payoff."}]
            moments = [{"start_time": 0, "end_time": 8, "title": "The Setup and Payoff"}]
            outcomes = {}
            for style in ("fast", "podcast", "tutorial"):
                with self.subTest(style=style):
                    result = crop_highlights_local(
                        str(source), moments, out_dir=str(Path(temporary) / style), aspect_ratio="9:16",
                        framing="center", caption_style="classic", segments=segments, editing_style=style,
                        minimum_duration=6,
                    )[0]
                    self.assertTrue(result["clip_url"], result.get("error"))
                    durations = json.loads(subprocess.check_output([
                        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
                        "-of", "json", result["clip_url"],
                    ]))["streams"]
                    durations = {stream["codec_type"]: float(stream["duration"]) for stream in durations}
                    self.assertAlmostEqual(durations["video"], durations["audio"], delta=.12)
                    subtitles = Path(result["caption_path"]).read_text(encoding="utf-8")
                    for text in ("Opening.", "Second sentence.", "Payoff."):
                        self.assertIn(text, subtitles)
                    if style == "tutorial":
                        self.assertNotIn("pauses_trimmed", result)
                        self.assertIn("00:00:06,600", subtitles)
                    else:
                        self.assertGreater(result["pauses_trimmed"], 0)
                        self.assertLessEqual(6, result["duration_seconds"])
                        self.assertNotIn("00:00:06,600", subtitles)
                    outcomes[style] = durations["video"]
            self.assertLess(outcomes["fast"], outcomes["podcast"])
            self.assertLess(outcomes["podcast"], outcomes["tutorial"])

    def test_gameplay_renders_silent_footage_without_a_ranking_provider(self):
        with TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "game.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=15:d=9",
                "-c:v", "libx264", str(source),
            ], check=True, capture_output=True)
            clips = generate_shorts("", uploaded_video=source, editing_style="gameplay", caption_style="off",
                                    num_clips=1, clip_length=(3, 6), out_dir=str(work / "render"))
            self.assertEqual(len(clips), 1)
            clip = clips[0]
            self.assertTrue(clip["clip_url"], clip.get("error"))
            self.assertGreaterEqual(clip["end_time"] - clip["start_time"], 3)
            self.assertLessEqual(clip["end_time"] - clip["start_time"], 6)
