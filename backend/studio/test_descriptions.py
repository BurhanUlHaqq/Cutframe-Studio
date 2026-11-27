import json
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from shorts_generator.local.llm import ranking_config

from .descriptions import DescriptionError, generate_descriptions
from .exports import job_directory
from .models import Job, WorkspaceAISettings
from .worker import enrich_descriptions


class DescriptionTests(TestCase):
    def setUp(self):
        self.client.get(reverse("session"))
        self.owner = self.client.session["studio_owner"]
        self.config = {"provider": "compatible", "model": "rank-fixture", "base_url": "http://localhost:7860/v1"}
        WorkspaceAISettings.objects.create(owner=self.owner, config=self.config)

    def test_custom_model_and_platform_generate_a_reviewable_draft_with_grounded_input(self):
        settings_url = reverse("description_settings")
        result = self.client.post(settings_url, {"model": "writing-fixture", "automatic": True}, content_type="application/json")
        self.assertEqual(result.json()["connection"]["model"], "writing-fixture")
        self.assertEqual(WorkspaceAISettings.objects.get(pk=self.owner).config, self.config)
        self.assertEqual(Client().get(settings_url).json()["settings"],
                         {"provider": "", "base_url": "", "model": "", "automatic": False})
        clip = {"name": "clip-01", "title": "The final rally", "description": "The original description.", "has_captions": True}
        job = Job.objects.create(owner=self.owner, clip_count=1, status=Job.Status.COMPLETE, clips=[clip], language="en")
        with TemporaryDirectory() as temporary, override_settings(STUDIO_DATA_DIR=Path(temporary)):
            files = job_directory(job.id) / "files"
            files.mkdir(parents=True)
            (files / "clip-01.srt").write_text("1\n00:00:00,000 --> 00:00:02,100\nThe sides are tied at eleven.\n", encoding="utf-8")
            endpoint = reverse("draft_description", args=[job.id, 0])
            self.assertEqual(Client().post(endpoint, {}, content_type="application/json").status_code, 404)
            write = Mock(return_value=json.dumps({"descriptions": [{"id": 0, "description":
                "The sides are tied at eleven. Watch how a crosscourt return changes the rally.\n\nWhich shot would you take? #FYP",
                "hashtags": ["FYP", "CrosscourtReturn", "#crosscourtreturn", "TableTennis"]}]}))
            with patch("studio.descriptions.ranking_client", return_value=nullcontext(write)) as model:
                result = self.client.post(endpoint, {"platform": "instagram"}, content_type="application/json")
            self.assertEqual(result.status_code, 200, result.content)
            self.assertEqual(model.call_args.args[0].model, "writing-fixture")
            self.assertIn("Instagram Reels", write.call_args.args[0])
            self.assertIn("sides are tied at eleven", write.call_args.args[0])
            self.assertIn("Which shot would you take?\n\n#CrosscourtReturn #TableTennis", result.json()["description"])
            self.assertNotIn("FYP", result.json()["description"])
            self.assertEqual(Job.objects.get(pk=job.id).clips[0]["description"], clip["description"])

    def test_independent_writer_connection_is_used_for_discovery_and_renders(self):
        settings_url = reverse("description_settings")
        credentials = {"provider": "compatible", "base_url": "http://host.docker.internal:11434/v1",
                       "model": "writer-local", "api_key": "writer-secret", "automatic": True}
        result = self.client.post(settings_url, credentials, content_type="application/json")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertTrue(result.json()["has_key"])
        self.assertNotIn("writer-secret", result.content.decode())
        row = WorkspaceAISettings.objects.get(pk=self.owner)
        self.assertEqual(row.config, self.config)
        self.assertNotIn("writer-secret", str(row.description_connection))

        model_list = reverse("ai_models") + "?task=descriptions"
        with patch("studio.views.list_ranking_models", return_value=["writer-local"]) as discover:
            models = self.client.post(model_list, {}, content_type="application/json")
        self.assertEqual(models.json(), {"models": ["writer-local"]})
        self.assertEqual(discover.call_args.args[0].base_url, credentials["base_url"])

        job = Job(owner=self.owner, language="en")
        clips = [{"title": "Rally", "clip_url": "fixture.mp4", "description": "Original"}]
        with patch("studio.worker.generate_descriptions", return_value={0: "Updated"}) as write:
            enrich_descriptions(job, clips, self.config, {}, Mock())
        self.assertEqual(write.call_args.args[0].model, credentials["model"])
        self.assertEqual(write.call_args.args[0].api_key, credentials["api_key"])
        self.assertEqual(clips[0]["description"], "Updated")

        inherited = self.client.post(settings_url, {"provider": "", "model": ""}, content_type="application/json")
        self.assertEqual(inherited.json()["connection"]["model"], self.config["model"])
        row.refresh_from_db()
        self.assertEqual(row.description_connection, {})

    def test_worker_batches_opt_in_copy_and_keeps_fallback_after_provider_failure(self):
        job = Job(owner=self.owner, language="en")
        clips = [{"title": str(index), "clip_url": "fixture.mp4", "description": "Original"} for index in range(13)]
        with patch("studio.worker.generate_descriptions") as write:
            enrich_descriptions(job, clips, self.config, {}, Mock())
        write.assert_not_called()
        WorkspaceAISettings.objects.filter(pk=self.owner).update(description_model="writing-fixture", description_automatic=True)
        report = Mock()
        with patch("studio.worker.generate_descriptions", side_effect=[{1: "Refreshed"}, DescriptionError("Provider error.")]) as write:
            enrich_descriptions(job, clips, self.config, {}, report)
        self.assertEqual([len(call.args[1]) for call in write.call_args_list], [12, 1])
        self.assertEqual(clips[1]["description"], "Refreshed")
        self.assertEqual(clips[12]["description"], "Original")
        self.assertTrue(any(call.kwargs.get("level") == "warning" for call in report.call_args_list))

    def test_platform_limit_and_invalid_reply_do_not_allow_generic_or_unbounded_descriptions(self):
        model = ranking_config("compatible", "writer", "http://localhost:7860/v1")
        long = "Review how the players open the final rally. " * 60
        reply = {"descriptions": [{"id": 10, "description": "Wrong clip."},
                                  {"id": True, "description": "Invalid id."},
                                  {"id": 0, "description": long, "hashtags": ["Viral", "TableTennis"]}]}
        with patch("studio.descriptions.ranking_client", return_value=nullcontext(lambda _: json.dumps(reply))):
            description = generate_descriptions(model, [{"title": "Rally"}], platform="tiktok")[0]
        self.assertLessEqual(len(description), 320)
        self.assertTrue(description.endswith("#TableTennis"))
        with patch("studio.descriptions.ranking_client", return_value=nullcontext(lambda _: "not JSON")):
            with self.assertRaises(DescriptionError):
                generate_descriptions(model, [{"title": "Rally"}])
