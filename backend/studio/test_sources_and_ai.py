from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from shorts_generator.local.llm import check_connection, list_ranking_models, ranking_config

from publishing.config import client_config

from .ai_settings import resolve_ai_options, seal_api_key
from .models import InstallationSettings, Job, WorkspaceAISettings
from .uploads import uploaded_source
from .worker import process_next_job


class UploadTests(TestCase):
    @staticmethod
    def file(name="My source.mov", content=b"video payload"):
        return SimpleUploadedFile(name, content, content_type="video/quicktime")

    def test_upload_uses_its_own_file_and_can_be_retried(self):
        with TemporaryDirectory() as directory, override_settings(STUDIO_DATA_DIR=Path(directory)):
            response = self.client.post(reverse("upload_job"), {"video": self.file(), "clip_count": 2})
            self.assertEqual(response.status_code, 201)
            result = response.json()
            self.assertEqual((result["source_type"], result["source_name"], result["clip_count"]),
                             ("upload", "My source.mov", 2))
            self.assertEqual(result["source_url"], "")
            stored = uploaded_source(result["id"])
            self.assertEqual(stored.read_bytes(), b"video payload")
            self.assertEqual(Client().get(reverse("detail", args=[result["id"]])).status_code, 404)
            with patch("studio.worker.generate_shorts", side_effect=RuntimeError("worker failure")) as generate:
                process_next_job()
                self.assertEqual(generate.call_args.args, ("",))
                self.assertEqual(generate.call_args.kwargs["uploaded_video"], stored)
                self.assertEqual(self.client.post(reverse("retry", args=[result["id"]])).status_code, 200)
                process_next_job()
                self.assertEqual(generate.call_args.kwargs["uploaded_video"].read_bytes(), b"video payload")

    @override_settings(STUDIO_MAX_UPLOAD_BYTES=32)
    def test_invalid_extensions_oversize_uploads_and_csrf(self):
        url = reverse("upload_job")
        self.assertIn("video", self.client.post(url, {"video": self.file("invalid.txt")}).json()["fields"])
        self.assertIn("video", self.client.post(url, {"video": self.file(content=b"x" * 33)}).json()["fields"])
        self.assertEqual(self.client.post(url, {"video": self.file(content=b"x" * (65 * 1024))}).status_code, 413)
        self.assertEqual(Client(enforce_csrf_checks=True).post(url, {"video": self.file()}).status_code, 403)
        self.assertFalse(Job.objects.exists())


class AISettingsTests(TestCase):
    installation_key = "test-installation-key"
    local = {
        "provider": "compatible", "model": "local-model",
        "base_url": "http://localhost:1234/v1", "whisper_model": "small",
    }

    def setUp(self):
        InstallationSettings.objects.create(ai={"provider": "compatible", "profiles": {
            "compatible": {"base_url": "http://host.docker.internal:11434/v1",
                           "model": "installation-model", "secret": seal_api_key(self.installation_key)},
            "openai": {"model": "test-openai-model", "secret": seal_api_key("test-openai-key")},
        }})

    def test_installation_defaults_and_saved_keys_do_not_cross_endpoints(self):
        url = reverse("ai_settings")
        response = self.client.get(url)
        self.assertEqual(response.json()["connection"]["model"], "installation-model")
        self.assertNotContains(response, self.installation_key)
        secret = "test-custom-endpoint-key"
        saved = self.client.post(url, {**self.local, "api_key": secret}, content_type="application/json")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["connection"]["key_source"], "saved")
        self.assertNotContains(saved, secret)
        self.assertNotIn(secret, str(WorkspaceAISettings.objects.get().config))
        self.assertEqual(Client().get(url).json()["settings"]["provider"], "")

        with patch("studio.views.check_connection") as check:
            check_url = reverse("ai_connection")
            for endpoint, key in [(self.local["base_url"], secret), ("https://other.example/v1", "")]:
                response = self.client.post(check_url, {**self.local, "base_url": endpoint}, content_type="application/json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(check.call_args.args[0].api_key, key)
        settings = self.client.get(url).json()["settings"]
        self.assertEqual({name: settings[name] for name in self.local}, self.local)
        removed = self.client.post(url, {**self.local, "remove_key": True}, content_type="application/json")
        self.assertEqual(removed.json()["connection"]["key_source"], "")
        self.assertNotIn("secret", WorkspaceAISettings.objects.get().config)
        self.assertEqual(resolve_ai_options({"provider": "openai"}).api_key, "test-openai-key")

    def test_transcription_tab_saves_without_changing_ranking_profiles(self):
        url = reverse("ai_settings")
        word_options = {"whisper_model": "small", "whisper_device": "cpu", "whisper_vad": "on"}
        scoped = self.client.post(url + "?scope=installation&task=transcription", word_options,
                                  content_type="application/json")
        self.assertEqual(scoped.status_code, 200, scoped.content)
        self.assertEqual(scoped.json()["connection"]["model"], "installation-model")
        self.assertEqual(scoped.json()["connection"]["key_source"], "saved")

        self.client.post(url, self.local, content_type="application/json")
        written = self.client.post(url + "?task=transcription", {"whisper_device": "cuda"},
                                   content_type="application/json")
        self.assertEqual(written.json()["settings"]["whisper_device"], "cuda")
        self.assertEqual(written.json()["connection"]["base_url"], self.local["base_url"])
        rank = self.client.post(url, {"model": "new-ranker"}, content_type="application/json")
        self.assertEqual(rank.json()["settings"]["whisper_device"], "cuda")

    def test_ai_settings_are_csrf_protected_and_validate_endpoints(self):
        client = Client(enforce_csrf_checks=True)
        url = reverse("ai_settings")
        self.assertEqual(client.post(url, self.local, content_type="application/json").status_code, 403)
        token = client.get(reverse("session")).json()["csrf_token"]
        for endpoint in ("file:///tmp/service", "https://user:password@example.com/v1", "http://example.com/v1"):
            with self.subTest(endpoint=endpoint):
                response = client.post(url, {**self.local, "base_url": endpoint}, content_type="application/json",
                                       HTTP_X_CSRFTOKEN=token)
                self.assertIn("base_url", response.json()["fields"])
        self.assertFalse(WorkspaceAISettings.objects.exists())

    def test_model_discovery_uses_draft_connection_without_requiring_a_selected_model(self):
        client = Client(enforce_csrf_checks=True)
        url = reverse("ai_models")
        draft = {**self.local, "model": "", "api_key": "test-draft-credential"}
        self.assertEqual(client.post(url, draft, content_type="application/json").status_code, 403)
        token = client.get(reverse("session")).json()["csrf_token"]
        with patch("studio.views.list_ranking_models", return_value=["local-model-a", "local-model-b"]) as fetch:
            response = client.post(url, draft, content_type="application/json", HTTP_X_CSRFTOKEN=token)
            self.assertEqual(response.json(), {"models": ["local-model-a", "local-model-b"]})
            config = fetch.call_args.args[0]
            self.assertEqual((config.provider, config.model, config.base_url), ("compatible", "", self.local["base_url"]))
            self.assertEqual(config.api_key, draft["api_key"])
        self.assertFalse(WorkspaceAISettings.objects.exists())

    def test_browser_uses_model_override_with_installation_key(self):
        data = {"provider": "", "model": "owner-selected-model"}
        saved = self.client.post(reverse("ai_settings"), data, content_type="application/json").json()
        self.assertEqual(saved["settings"]["model"], data["model"])
        self.assertEqual(saved["connection"]["key_source"], "installation")
        self.assertEqual(saved["installation"]["model"], "installation-model")
        with patch("studio.views.check_connection") as check:
            self.assertEqual(self.client.post(reverse("ai_connection"), data, content_type="application/json").status_code, 200)
            config = check.call_args.args[0]
            self.assertEqual(config.model, data["model"])
            self.assertEqual(config.api_key, self.installation_key)
        self.assertEqual(Client().get(reverse("ai_settings")).json()["connection"]["model"], "installation-model")

    def test_catalogue_filters_provider_metadata_without_generation(self):
        with patch("openai.OpenAI") as openai, patch("google.genai.Client") as gemini:
            openai_client = openai.return_value.__enter__.return_value
            openai_client.models.list.return_value = [
                SimpleNamespace(id=model) for model in ("gpt-4o-mini", "whisper-1", "gpt-4o", "gpt-4o")
            ]
            self.assertEqual(list_ranking_models(resolve_ai_options({"provider": "openai"})), ["gpt-4o", "gpt-4o-mini"])
            openai_client.chat.completions.create.assert_not_called()

            gemini_client = gemini.return_value.__enter__.return_value
            gemini_client.models.list.return_value = [
                SimpleNamespace(name="models/gemini-test", supported_actions=["generateContent"]),
                SimpleNamespace(name="models/gemini-test-tts", supported_actions=["generateContent"]),
                SimpleNamespace(name="models/embedding-test", supported_actions=["embedContent"]),
            ]
            self.assertEqual(list_ranking_models(ranking_config("gemini", api_key="test-gemini-key")), ["gemini-test"])
            gemini_client.models.generate_content.assert_not_called()

    def test_cloud_connection_checks_use_model_metadata(self):
        with patch("openai.OpenAI") as openai, patch("google.genai.Client") as gemini:
            openai_client = openai.return_value.__enter__.return_value
            openai_client.models.list.return_value = [SimpleNamespace(id="test-openai-model")]
            check_connection(resolve_ai_options({"provider": "openai"}))
            self.assertEqual(openai.call_args.kwargs["base_url"], "https://api.openai.com/v1")
            openai_client.models.list.assert_called_once_with()
            openai_client.chat.completions.create.assert_not_called()

            gemini_client = gemini.return_value.__enter__.return_value
            check_connection(ranking_config("gemini", "test-gemini-model", api_key="test-gemini-key"))
            gemini_client.models.get.assert_called_once_with(model="test-gemini-model")
            gemini_client.models.generate_content.assert_not_called()

    def test_installation_ai_and_storage_take_effect_without_restarting(self):
        url = reverse("ai_settings") + "?scope=installation"
        key = "test-new-shared-key"
        data = {"provider": "gemini", "model": "new-model", "api_key": key, "whisper_device": "cpu",
                "whisper_vad": "on", "vad_parameters": {"threshold": 0.65}}
        response = self.client.post(url, data, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, key)
        self.assertNotIn(key, str(InstallationSettings.objects.get().ai))
        self.assertEqual(resolve_ai_options({}).api_key, key)
        self.assertEqual(Client().get(reverse("ai_settings")).json()["connection"]["model"], "new-model")

        self.assertEqual(self.client.post(reverse("storage_settings"), {"max_upload_mib": 5},
                                          content_type="application/json").status_code, 200)
        self.assertEqual(self.client.get(reverse("jobs")).json()["upload"]["max_bytes"], 5 * 1024**2)
        self.assertEqual(resolve_ai_options({}).api_key, key)
        self.assertEqual(self.client.post(url, {"remove_key": True}, content_type="application/json").status_code, 200)
        self.assertEqual(resolve_ai_options({}).api_key, "")

    def test_legacy_file_import_encrypts_app_credentials_and_leaves_saved_defaults(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "OPENAI_API_KEY=keep-existing\nGEMINI_API_KEY=fixture-new\nLOCAL_WHISPER_MODEL=tiny\n"
                "PUBLISH_YOUTUBE_CLIENT_ID=fixture-client\nPUBLISH_YOUTUBE_CLIENT_SECRET=fixture-secret\n",
                encoding="utf-8",
            )
            output = StringIO()
            call_command("import_legacy_settings", str(path), stdout=output)
            call_command("import_legacy_settings", str(path), stdout=output)
        self.assertNotIn("fixture-secret", output.getvalue())
        self.assertEqual(resolve_ai_options({"provider": "openai"}).api_key, "test-openai-key")
        self.assertEqual(resolve_ai_options({"provider": "gemini"}).api_key, "fixture-new")
        self.assertEqual(client_config("youtube").secret, "fixture-secret")
        self.assertEqual(InstallationSettings.objects.get().ai["whisper_model"], "tiny")
