import socket
import subprocess
from contextlib import nullcontext
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from yt_dlp.extractor import get_info_extractor

from .models import Download
from shorts_generator.local.network import PlatformDownloader, connect_public
from .sources import PLAYLISTS, SOURCES, normalize_source, normalize_target
from .worker import download_directory, fetch_video, probe_download, process_next, run_queue, video_within_8k


LINKS = {
    "YouTube": ("https://youtu.be/BaW_jenozKc?list=example", "Youtube"),
    "Vimeo": ("https://vimeo.com/76979871", "Vimeo"),
    "Dailymotion": ("https://dai.ly/x2iuewm", "Dailymotion"),
    "TikTok": ("https://vm.tiktok.com/ZMmock1/", "TikTokVM"),
    "Instagram": ("https://www.instagram.com/reel/ABCdefgh01/?utm_source=share", "Instagram"),
    "Twitch": ("https://clips.twitch.tv/TestClip", "TwitchClips"),
    "Reddit": ("https://www.reddit.com/r/videos/comments/abc123/title/", "Reddit"),
}


PLAYLIST = "https://www.youtube.com/playlist?list=PLCutframePublicFixture"


class SourceValidationTests(SimpleTestCase):
    def test_playlist_links_match_supported_extractors_and_keep_watch_links_explicit(self):
        youtube = LINKS["YouTube"][0].split("?")[0] + "?" + PLAYLIST.split("?")[1]
        self.assertEqual(normalize_target(youtube)[2], False)
        self.assertEqual(normalize_target(youtube, linked_playlist=True), (PLAYLIST, "YouTube", True))
        for link, key in ((PLAYLIST, "YoutubeTab"), ("https://vimeo.com/showcase/123456", "VimeoAlbum"),
                          ("https://www.dailymotion.com/playlist/x6abc", "DailymotionPlaylist")):
            with self.subTest(link=link):
                url, platform, playlist = normalize_target(link)
                self.assertTrue(playlist)
                self.assertTrue(get_info_extractor(key).suitable(url))
                with PlatformDownloader({"quiet": True}, PLAYLISTS[platform]) as downloader:
                    self.assertEqual(downloader.get_info_extractor(key).ie_key(), key)

    def test_8k_limit_covers_portrait_and_landscape(self):
        self.assertTrue(video_within_8k({"width": 7680, "height": 4320}))
        self.assertTrue(video_within_8k({"width": 4320, "height": 7680}))
        self.assertFalse(video_within_8k({"width": 8192, "height": 4320}))
        self.assertFalse(video_within_8k({"width": 6000, "height": 5000}))

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
        with patch("shorts_generator.local.network.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("127.0.0.1", 443)), (2, 1, 6, "", ("10.0.0.1", 443)),
        ]), patch("shorts_generator.local.network.socket.socket") as open_socket:
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
                            "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=44100", "-t", "1",
                            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(sample)], check=True)
            audio_destination = root / "audio"
            audio_destination.mkdir()
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
                          patch("shorts_generator.local.network.connect_public", wraps=socket.create_connection) as opened):
                        _info, path = fetch_video(job, destination, progress)
                        job.output, job.audio_bitrate = "mp3", 320
                        _audio_info, mp3 = fetch_video(job, audio_destination, progress)
                finally:
                    server.shutdown()
                    thread.join()
            opened.assert_called()
            progress.transfer.assert_called()
            self.assertEqual(path.read_bytes(), sample.read_bytes())
            self.assertEqual(mp3.suffix, ".mp3")
            self.assertGreater(probe_download(mp3, audio_only=True)[0], 0.9)
            bitrate = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=bit_rate",
                                               "-of", "default=nw=1:nk=1", str(mp3)])
            self.assertEqual(int(bitrate), 320000)


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
            client.params = {}
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

    def test_playlist_preserves_completed_entries_across_stop_and_retry_and_streams_zip(self):
        data = {"urls": [PLAYLIST], "output": "mp3", "audio_bitrate": 256, "playlist_start": 4, "playlist_end": 5}
        result = self.client.post(self.endpoint, data, content_type="application/json")
        self.assertEqual(result.status_code, 201, result.content)
        job = Download.objects.get(pk=result.json()["downloads"][0]["id"])
        self.assertTrue(job.is_playlist)
        self.assertEqual(job.output, "mp3")
        stop = reverse("downloads:action", args=[job.pk, "stop"])
        media = Path(self.directory.name) / "audio.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=250", "-t", "1",
                        "-c:a", "libmp3lame", str(media)], check=True)
        discover = Mock()
        discover.extract_info.return_value = {"title": "Tracks", "entries": [
            {"title": "First", "url": "https://youtu.be/BaW_jenozKc"},
            {"title": "Second", "url": "https://youtu.be/YE7VzlLtp-4"},
        ]}

        def save(item, directory, progress):
            path = directory / "video.mp3"
            path.write_bytes(media.read_bytes())
            return {"title": item.title}, path

        def stop_at_second(item, directory, progress):
            if item.position == 5:
                self.assertEqual(self.client.post(stop).json()["status"], "cancelling")
                progress.report(activity="Downloading audio")
            return save(item, directory, progress)

        with (patch("downloads.worker.PlatformDownloader", return_value=nullcontext(discover)) as client,
              patch("downloads.worker.fetch_video", side_effect=stop_at_second)):
            process_next()
        self.assertEqual(client.call_args.args[0]["playlist_items"], "4:5")
        row = self.client.get(self.endpoint).json()["downloads"][0]
        self.assertEqual(row["status"], "cancelled")
        self.assertEqual((row["playlist"]["total"], row["playlist"]["ready"]), (2, 1))
        items = self.client.get(row["playlist"]["items_url"]).json()["items"]
        self.assertEqual([(entry["position"], entry["status"]) for entry in items], [(4, "complete"), (5, "cancelled")])
        self.assertEqual(Client().get(row["playlist"]["items_url"]).status_code, 404)
        partial_range = self.client.get(items[0]["file_url"], HTTP_RANGE="bytes=0-15")
        self.assertEqual((partial_range.status_code, partial_range["Content-Type"]), (206, "audio/mpeg"))
        self.assertEqual(b"".join(partial_range.streaming_content), media.read_bytes()[:16])

        retry = reverse("downloads:action", args=[job.pk, "retry"])
        self.assertEqual(self.client.post(retry).json()["status"], "queued")
        with patch("downloads.worker.fetch_video", side_effect=save) as worker:
            process_next()
        self.assertEqual([call.args[0].position for call in worker.call_args_list], [5])
        history = self.client.get(self.endpoint).json()
        row = history["downloads"][0]
        self.assertEqual((history["total"], row["status"], row["playlist"]["ready"]), (1, "complete", 2))
        self.assertIsNone(row["file_url"])
        self.assertEqual(Client().get(row["playlist"]["zip_url"]).status_code, 404)
        zipped = self.client.get(row["playlist"]["zip_url"])
        self.assertIn("Tracks.zip", zipped["Content-Disposition"])
        with ZipFile(BytesIO(b"".join(zipped.streaming_content))) as archive:
            self.assertEqual(archive.namelist(), ["004 - First.mp3", "005 - Second.mp3"])
            self.assertEqual(archive.read("004 - First.mp3"), media.read_bytes())

    def test_playlist_and_media_options_are_validated_before_queueing(self):
        for fields in ({"playlist_start": 0}, {"playlist_start": 2, "playlist_end": 102},
                       {"playlist_start": 5, "playlist_end": 3}, {"audio_bitrate": 111}, {"output": "wav"}):
            with self.subTest(fields=fields):
                invalid = self.client.post(self.endpoint, {"urls": [PLAYLIST], **fields}, content_type="application/json")
                self.assertEqual(invalid.status_code, 400)
        result = self.queue(PLAYLIST, quality="4320")
        self.assertEqual(result.status_code, 201, result.content)
        self.assertEqual(result.json()["downloads"][0]["quality"], "4320")

    def test_download_timestamp_cuts_validate_individual_sources(self):
        fields = {"source_start": "1:00.25", "source_end": "70"}
        queued = self.client.post(self.endpoint, {"urls": [LINKS["YouTube"][0]], **fields}, content_type="application/json")
        self.assertEqual(queued.status_code, 201, queued.content)
        row = queued.json()["downloads"][0]
        self.assertEqual((row["source_start_ms"], row["source_end_ms"]), (60250, 70000))
        playlist = self.client.post(self.endpoint, {"urls": [PLAYLIST], **fields}, content_type="application/json")
        self.assertEqual(playlist.status_code, 400)
        self.assertIn("Timestamp cuts apply to individual videos", playlist.json()["error"])

    def test_json_batches_require_csrf_and_history_is_paginated(self):
        secured = Client(enforce_csrf_checks=True)
        payload = {"urls": [LINKS["Vimeo"][0]]}
        self.assertEqual(secured.post(self.endpoint, payload, content_type="application/json").status_code, 403)
        for _ in range(13):
            self.queue(*payload["urls"])
        history = self.client.get(self.endpoint + "?page=2").json()
        self.assertEqual((len(history["downloads"]), history["pages"], history["total"]), (1, 2, 13))
