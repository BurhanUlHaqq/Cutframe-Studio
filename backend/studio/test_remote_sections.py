import re
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from shorts_generator.local.media import InvalidSourceVideo, validate_local_video
from shorts_generator.local.network import PlatformDownloader
from shorts_generator.local.sections import extract_section, read_block
from shorts_generator.pipeline import prepare_bounded_source


class SectionDownloadTests(SimpleTestCase):
    def test_section_reads_only_bounded_bytes_and_keeps_the_correct_frames(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.mp4"
            output = root / "selected.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=160x90:r=10:d=48",
                "-f", "lavfi", "-i", "color=lime:s=160x90:r=10:d=12",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[video]", "-map", "[video]",
                "-c:v", "libx264", "-preset", "ultrafast", "-g", "10", "-b:v", "1200k", "-minrate", "1200k",
                "-maxrate", "1200k", "-bufsize", "1200k", "-x264-params", "nal-hrd=cbr", str(source),
            ], check=True, capture_output=True)
            data = source.read_bytes()
            ranges = []

            class Origin(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_GET(self):
                    requested = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
                    if not requested:
                        ranges.append(None)
                        self.send_error(400)
                        return
                    start, end = map(int, requested.groups())
                    end = min(end, len(data) - 1)
                    ranges.append((start, end))
                    block = data[start:end + 1]
                    self.send_response(206)
                    self.send_header("Content-Length", str(len(block)))
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                    self.end_headers()
                    self.wfile.write(block)

            with ThreadingHTTPServer(("127.0.0.1", 0), Origin) as server:
                running = Thread(target=server.serve_forever, daemon=True)
                running.start()
                try:
                    info = {"duration": 60, "title": "Fixture", "url": f"http://127.0.0.1:{server.server_port}/source.mp4"}
                    with (patch.object(PlatformDownloader, "extract_info", return_value=info) as discover,
                          patch("shorts_generator.local.network.connect_public", wraps=socket.create_connection)):
                        _, start, end = extract_section(
                            "https://example.test/source", output, (50200, 53200), extractors=("youtube",),
                        )
                    discover.assert_called_once_with("https://example.test/source", download=False)
                finally:
                    server.shutdown()
                    running.join()

            self.assertEqual((start, end), (50.2, 53.2))
            self.assertTrue(ranges)
            self.assertNotIn(None, ranges)
            self.assertLess(sum(last - first + 1 for first, last in ranges), len(data) / 2)
            self.assertAlmostEqual(validate_local_video(output, require_audio=False)[0], 3, delta=0.1)
            pixel = subprocess.check_output([
                "ffmpeg", "-v", "error", "-i", str(output), "-frames:v", "1", "-vf", "scale=1:1",
                "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
            ])
            self.assertGreater(pixel[1], max(pixel[0], pixel[2]) + 100)

    def test_denied_direct_stream_tries_only_the_overlapping_hls_fragments(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "stream.m3u8"
            output = root / "selected.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=160x90:r=10:d=8",
                "-f", "lavfi", "-i", "color=lime:s=160x90:r=10:d=4",
                "-f", "lavfi", "-i", "color=blue:s=160x90:r=10:d=12",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=24",
                "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[video]", "-map", "[video]", "-map", "3:a",
                "-c:v", "libx264", "-g", "20", "-sc_threshold", "0", "-c:a", "aac",
                "-hls_time", "2", "-hls_segment_type", "fmp4", "-hls_list_size", "0", str(manifest),
            ], check=True, capture_output=True)
            requests = []

            class Origin(BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

                def do_GET(self):
                    requests.append(self.path)
                    if self.path == "/source.mp4":
                        self.send_error(403)
                        return
                    data = (root / self.path.removeprefix("/")).read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

            with ThreadingHTTPServer(("127.0.0.1", 0), Origin) as server:
                running = Thread(target=server.serve_forever, daemon=True)
                running.start()
                try:
                    origin = f"http://127.0.0.1:{server.server_port}"
                    direct = {"url": origin + "/source.mp4", "format_id": "direct", "protocol": "http",
                              "ext": "mp4", "vcodec": "h264", "acodec": "aac"}
                    hls = {**direct, "url": origin + "/stream.m3u8", "format_id": "hls", "protocol": "m3u8_native"}
                    info = {**direct, "duration": 24, "formats": [hls, direct]}
                    with (patch.object(PlatformDownloader, "extract_info", return_value=info),
                          patch("shorts_generator.local.network.connect_public", wraps=socket.create_connection)):
                        _, start, end = extract_section(
                            "https://example.test/source", output, (9200, 12800), extractors=("youtube",),
                        )
                finally:
                    server.shutdown()
                    running.join()

            self.assertEqual((start, end), (9.2, 12.8))
            self.assertEqual(requests, ["/source.mp4", "/stream.m3u8", "/init.mp4",
                                        "/stream4.m4s", "/stream5.m4s", "/stream6.m4s"])
            self.assertAlmostEqual(validate_local_video(output)[0], 3.6, delta=0.1)

            def pixel_at(position):
                return subprocess.check_output([
                    "ffmpeg", "-v", "error", "-ss", position, "-i", str(output), "-frames:v", "1",
                    "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
                ])

            first, last = pixel_at("0"), pixel_at("3.4")
            self.assertGreater(first[1], max(first[0], first[2]) + 100)
            self.assertGreater(last[2], max(last[0], last[1]) + 100)

    def test_ignored_range_never_reads_the_full_response_body(self):
        client = MagicMock()
        response = client.urlopen.return_value.__enter__.return_value
        response.status = 200
        response.headers = {}
        with self.assertRaises(InvalidSourceVideo):
            read_block(client, {"url": "https://example.test/video.mp4"}, 8000, 8500)
        response.read.assert_not_called()

    def test_render_does_not_fall_back_to_a_full_video(self):
        with TemporaryDirectory() as directory:
            with (patch("shorts_generator.pipeline.download_youtube_section", side_effect=InvalidSourceVideo("No partial media")),
                  patch("shorts_generator.pipeline.download_youtube_local") as full):
                with self.assertRaisesMessage(InvalidSourceVideo, "No partial media"):
                    prepare_bounded_source("https://youtu.be/YE7VzlLtp-4", None, "720", (1000, 2000),
                                           directory, lambda *_args, **_kwargs: None)
            full.assert_not_called()
