import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from unittest import skipUnless

import cv2
from django.test import SimpleTestCase
import numpy as np

from shorts_generator.highlights import get_highlights
from shorts_generator.local.captions import build_cues, write_ass, write_srt
from shorts_generator.local.clipper import crop_clip_local
from shorts_generator.local.reframe import Framer, choose_targets
from shorts_generator.local.thumbnails import write_thumbnail


def ffmpeg_has_ass():
    if not shutil.which("ffmpeg"):
        return False
    result = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, check=True)
    return any(line.split()[1:2] == ["ass"] for line in result.stdout.splitlines())


class MediaFeaturesTests(SimpleTestCase):
    def test_clip_snaps_outward_and_discards_nested_duplicates(self):
        transcript = {"duration": 14, "segments": [{
            "start": 2.0, "end": 5.0, "text": "Here is the payoff.",
            "words": [
                {"start": 2.0, "end": 2.5, "text": " Here"},
                {"start": 2.5, "end": 2.85, "text": " is"},
                {"start": 2.85, "end": 3.2, "text": " the"},
                {"start": 3.4, "end": 5, "text": " payoff."},
            ],
        }]}
        replies = iter([
            '{"content_type":"tutorial","density":"medium"}',
            json.dumps({"highlights": [
                {"title": "Best", "start_time": 2.42, "end_time": 4.8, "score": 90, "hook_sentence": "Made up"},
                {"title": "Overlap", "start_time": 0, "end_time": 14, "score": 60},
                {"title": "Silence", "start_time": 9, "end_time": 13, "score": 70},
            ]}),
        ])
        ranked = get_highlights(transcript, llm_fn=lambda _: next(replies))["highlights"]
        self.assertEqual(len(ranked), 1)
        self.assertEqual((ranked[0]["start_time"], ranked[0]["end_time"]), (2.0, 5.0))
        self.assertEqual(ranked[0]["hook_sentence"], "Here is the payoff.")

    def test_caption_cues_are_relative_readable_and_escape_override_tags(self):
        segments = [{
            "start": 10, "end": 16, "text": "hello again",
            "words": [
                {"start": 10, "end": 10.4, "text": " hello"},
                {"start": 10.4, "end": 11, "text": " again"},
                {"start": 12.5, "end": 13, "text": r" {\pos(0,0)}"},
            ],
        }]
        cues = build_cues(segments, 10.1, 14)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].start, 0)
        self.assertLess(cues[0].end, cues[1].start)
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            write_srt(path / "clip.srt", cues)
            write_ass(path / "clip.ass", cues, "highlight", 360, 640)
            self.assertIn("00:00:00,000 --> 00:00:01,050", (path / "clip.srt").read_text())
            ass = (path / "clip.ass").read_text(encoding="utf-8")
            self.assertIn(r"{\k30}hello", ass)
            self.assertNotIn(r"{\pos(0,0)}", ass)

    def test_smart_planner_tracks_splits_and_preserves_wide_content(self):
        face = (65, 70, 2400)
        distant = (260, 75, 2300)
        self.assertEqual(choose_targets([face], 320, True, None), ("track", ((65, 70),)))
        self.assertEqual(choose_targets([distant, face], 320, True, None)[0], "split")
        self.assertEqual(choose_targets([face, distant, (145, 50, 2300)], 320, True, None), ("fit", ()))
        self.assertEqual(choose_targets([], 320, True, None), ("fit", ()))

        framer = Framer(cv2, (320, 180), (90, 160), "9:16", "auto", 24)
        image = np.full((180, 320, 3), 200, dtype=np.uint8)
        result = framer.frame(image, 0)
        self.assertEqual(result.shape, (160, 90, 3))
        self.assertEqual(framer.layout, "fit")
        self.assertGreater(result[80, 45].mean(), result[0, 45].mean())

    @skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_thumbnail_looks_beyond_a_dark_opening(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "base.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=black:s=360x640:r=12:d=2",
                 "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=12:duration=2",
                 "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-c:v", "libx264", str(video)],
                check=True,
            )
            cover = root / "thumbnail.jpg"
            write_thumbnail(video, cover)
            image = cv2.imread(str(cover))
            self.assertEqual(image.shape[:2], (640, 360))
            self.assertGreater(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).std(), 30)

    @skipUnless(ffmpeg_has_ass(), "FFmpeg with the ASS filter required")
    def test_captions_are_burned_into_final_mp4_and_cover_has_clean_footage(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "base.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=blue:s=640x360:r=24",
                 "-t", "2", "-c:v", "libx264", str(source)], check=True,
            )
            cues = build_cues([{"start": 0, "end": 1.8, "text": "Caption test"}], 0, 2)

            def pixels(file):
                return np.frombuffer(subprocess.check_output(
                    ["ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(file), "-frames:v", "1",
                     "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                ), dtype=np.uint8).astype(np.int16)

            clean = root / "clean.mp4"
            burned = root / "burned.mp4"
            thumbnail = root / "cover.jpg"
            crop_clip_local(str(source), 0, 2, "9:16", str(clean), framing="center")
            crop_clip_local(str(source), 0, 2, "9:16", str(burned), framing="center",
                            cues=cues, caption_style="highlight", thumbnail_path=thumbnail)
            self.assertGreater(np.count_nonzero(np.abs(pixels(clean) - pixels(burned)) > 30), 100)
            cover = cv2.imread(str(thumbnail))
            self.assertLess(cover[:, :, 1].max(), 20)
