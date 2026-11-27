import socket
import subprocess
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from yt_dlp.extractor import get_info_extractor

from .models import Download
from .network import PlatformDownloader, connect_public
from .sources import SOURCES, normalize_source
from .worker import download_directory, fetch_video, process_next, run_queue


LINKS = {
    "YouTube": ("https://youtu.be/BaW_jenozKc?list=example", "Youtube"),
    "Vimeo": ("https://vimeo.com/76979871", "Vimeo"),
    "Dailymotion": ("https://dai.ly/x2iuewm", "Dailymotion"),
    "TikTok": ("https://vm.tiktok.com/ZMmock1/", "TikTokVM"),
    "Instagram": ("https://www.instagram.com/reel/ABCdefgh01/?utm_source=share", "Instagram"),
    "Twitch": ("https://clips.twitch.tv/TestClip", "TwitchClips"),
    "Reddit": ("https://www.reddit.com/r/videos/comments/abc123/title/", "Reddit"),
}


class SourceValidationTests(SimpleTestCase):
    def test_supported_links_match_the_intended_installed_extractors(self):
        for platform, (source, key) in LINKS.items():
            with self.subTest(platform=platform):
                url, detected = normalize_source(source)
                self.assertEqual(detected, platform)
                self.assertTrue(get_info_extractor(key).suitable(url), url)
                with PlatformDownloader({"quiet": True}, SOURCES[platform][1]) as downloader:
                    self.assertEqual(downloader.get_info_extractor(key).ie_key(), key)
        self.assertEqual(normalize_source(LINKS["YouTube"][0])[0], "https://www.youtube.com/watch?v=BaW_jenozKc")

    def test_invalid_and_collection_links_are_rejected(self):
        for url in (
            "http://www.youtube.com/watch?v=BaW_jenozKc", "https://www.youtube.com.evil.invalid/watch?v=BaW_jenozKc",
            "https://www.youtube.com/playlist?list=abc", "https://vimeo.com:444/76979871",
            "https://username@vimeo.com/76979871", "https://www.instagram.com/reels/audio/123",
            "file:///tmp/movie.mp4", "https://127.0.0.1/video.mp4",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_source(url)

    def test_network_does_not_connect_to_private_addresses(self):
        with patch("downloads.network.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("127.0.0.1", 443)), (2, 1, 6, "", ("10.0.0.1", 443)),
        ]), patch("downloads.network.socket.socket") as open_socket:
            with self.assertRaisesMessage(OSError, "non-public"):
                connect_public(("fixture.invalid", 443))
        open_socket.assert_not_called()


class LocalTransferTests(SimpleTestCase):
    def test_yt_dlp_streams_a_playable_fixture_through_the_configured_transport(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sample = root / "source.mp4"
            destination = root / "download"
            destination.mkdir()
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12",
                            "-t", "1", "-c:v", "libx264", "-preset", "ultrafast", str(sample)], check=True)
            handler = partial(SimpleHTTPRequestHandler, directory=root)
            with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                job = SimpleNamespace(source_url=f"http://127.0.0.1:{server.server_port}/source.mp4",
                                      platform="Fixture", quality="480")
                progress = Mock()
                try:
                    # Only the isolated fixture can use the loopback transport.
                    with (patch.dict(SOURCES, {"Fixture": (set(), ("generic",))}),
                          patch("downloads.network.connect_public", wraps=socket.create_connection) as opened):
                        _info, path = fetch_video(job, destination, progress)
                finally:
                    server.shutdown()
                    thread.join()
            opened.assert_called()
            progress.transfer.assert_called()
            self.assertEqual(path.read_bytes(), sample.read_bytes())


class DownloadsTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = override_settings(STUDIO_DATA_DIR=Path(self.directory.name))
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.endpoint = reverse("downloads:list")
        self.client.get(reverse("session"))

    def queue(self, *sources, quality="720"):
        return self.client.post(self.endpoint, {"urls": sources, "quality": quality}, content_type="application/json")

    def test_batch_validation_is_atomic_and_normalized_links_are_deduplicated(self):
        source, _ = LINKS["YouTube"]
        result = self.queue(source, "https://example.org/video")
        self.assertEqual(result.status_code, 400)
        self.assertIn("Link 2", result.json()["error"])
        self.assertFalse(Download.objects.exists())
        result = self.queue(source, normalize_source(source)[0], LINKS["Vimeo"][0])
        self.assertEqual(result.status_code, 201, result.content)
        self.assertEqual([row["platform"] for row in result.json()["downloads"]], ["YouTube", "Vimeo"])
        history = self.client.get(self.endpoint).json()
        self.assertEqual((history["active"], history["total"]), (2, 2))
        self.assertEqual(Client().get(self.endpoint).json()["downloads"], [])

    def test_metadata_progress_and_saved_file_follow_the_real_worker_pipeline(self):
        queued = self.queue(LINKS["Vimeo"][0]).json()["downloads"][0]
        job = Download.objects.get(pk=queued["id"])
        sample = Path(self.directory.name) / "example.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12",
                        "-t", "1", "-c:v", "libx264", "-preset", "ultrafast", str(sample)], check=True)

        def provider(options, _extractors):
            client = Mock()
            client.__enter__ = Mock(return_value=client)
            client.__exit__ = Mock(return_value=False)

            def download(url, download):
                asset = Path(options["outtmpl"].replace("%(ext)s", "mp4"))
                options["progress_hooks"][0]({
                    "status": "downloading", "filename": str(asset), "downloaded_bytes": 512, "total_bytes": 1024,
                })
                job.refresh_from_db()
                self.assertEqual(job.bytes_received, 512)
                self.assertIn("Downloading", job.activity)
                asset.write_bytes(sample.read_bytes())
                return {"title": "Vimeo: footage", "width": 160, "height": 90}

            client.extract_info = download
            return client

        with patch("downloads.worker.PlatformDownloader", side_effect=provider):
            self.assertTrue(process_next())
        job.refresh_from_db()
        self.assertEqual(job.status, Download.Status.COMPLETE, job.activity)
        self.assertEqual((job.extension, job.duration), ("mp4", 1.0))
        current = self.client.get(self.endpoint).json()["downloads"][0]
        self.assertEqual(current["title"], "Vimeo: footage")
        file = current["file_url"]
        self.assertEqual(Client().get(file).status_code, 404)
        response = self.client.get(file)
        self.assertEqual(b"".join(response.streaming_content), sample.read_bytes())
        self.assertIn('filename="Vimeo footage.mp4"', response["Content-Disposition"])
        deleted = reverse("downloads:action", args=[job.pk, "delete"])
        self.assertEqual(self.client.post(deleted).status_code, 200)
        self.assertFalse(download_directory(job.pk).exists())
        self.assertEqual(self.client.get(file).status_code, 404)

    def test_stop_restart_and_failure_remove_partial_files(self):
        queued = self.queue(LINKS["YouTube"][0]).json()["downloads"][0]
        job = Download.objects.get(pk=queued["id"])
        stop = reverse("downloads:action", args=[job.pk, "stop"])
        retry = reverse("downloads:action", args=[job.pk, "retry"])
        self.assertEqual(Client().post(stop).status_code, 404)
        self.assertEqual(self.client.post(stop).json()["status"], "cancelled")
        self.assertEqual(self.client.post(retry).json()["status"], "queued")

        def active_stop(_job, directory, progress):
            (directory / "video.mp4.part").write_bytes(b"partial")
            self.assertEqual(self.client.post(stop).json()["status"], "cancelling")
            progress.report(activity="Downloading")

        with patch("downloads.worker.fetch_video", side_effect=active_stop):
            process_next()
        job.refresh_from_db()
        self.assertEqual(job.status, Download.Status.CANCELLED)
        self.assertFalse(download_directory(job.pk).exists())

        download_directory(job.pk).mkdir(parents=True)
        Download.objects.filter(pk=job.pk).update(status=Download.Status.RUNNING)

        def failure(_job, directory, _progress):
            (directory / "video.mp4.part").write_bytes(b"partial")
            raise RuntimeError("remote returned 403 for private URL")

        with patch("downloads.worker.fetch_video", side_effect=failure):
            run_queue(once=True)
        job.refresh_from_db()
        self.assertEqual(job.status, Download.Status.FAILED)
        self.assertIn("source restricted", job.activity)
        self.assertFalse(download_directory(job.pk).exists())

    def test_json_batches_require_csrf_and_history_is_paginated(self):
        secured = Client(enforce_csrf_checks=True)
        payload = {"urls": [LINKS["Vimeo"][0]]}
        self.assertEqual(secured.post(self.endpoint, payload, content_type="application/json").status_code, 403)
        for _ in range(13):
            self.queue(*payload["urls"])
        history = self.client.get(self.endpoint + "?page=2").json()
        self.assertEqual((len(history["downloads"]), history["pages"], history["total"]), (1, 2, 13))
