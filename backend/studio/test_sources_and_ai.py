import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from shorts_generator.local.llm import check_connection, list_ranking_models, ranking_config

from .models import Job, WorkspaceAISettings
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
    environment = {
        "LLM_PROVIDER": "compatible", "LLM_BASE_URL": "http://host.docker.internal:11434/v1",
        "LLM_MODEL": "workspace-env-model", "LLM_API_KEY": "test-environment-key",
        "OPENAI_API_KEY": "test-openai-key", "OPENAI_MODEL": "test-openai-model",
    }
    local = {
        "provider": "compatible", "model": "local-model",
        "base_url": "http://localhost:1234/v1", "whisper_model": "small",
    }

    @patch.dict(os.environ, environment, clear=False)
    def test_environment_defaults_and_saved_keys_do_not_cross_endpoints(self):
        url = reverse("ai_settings")
        response = self.client.get(url)
        self.assertEqual(response.json()["connection"]["model"], "workspace-env-model")
        self.assertNotContains(response, self.environment["LLM_API_KEY"])
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
        self.assertEqual(self.client.get(url).json()["settings"], self.local)
        removed = self.client.post(url, {**self.local, "remove_key": True}, content_type="application/json")
        self.assertEqual(removed.json()["connection"]["key_source"], "")
        self.assertNotIn("secret", WorkspaceAISettings.objects.get().config)
        self.assertEqual(ranking_config("openai").api_key, "test-openai-key")

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

    @patch.dict(os.environ, environment | {"LLM_MODEL": ""})
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

    @patch.dict(os.environ, environment)
    def test_server_environment_uses_saved_model_override_with_environment_key(self):
        data = {"provider": "", "model": "owner-selected-model"}
        saved = self.client.post(reverse("ai_settings"), data, content_type="application/json").json()
        self.assertEqual(saved["settings"]["model"], data["model"])
        self.assertEqual(saved["connection"]["key_source"], "environment")
        self.assertEqual(saved["environment"]["model"], "workspace-env-model")
        with patch("studio.views.check_connection") as check:
            self.assertEqual(self.client.post(reverse("ai_connection"), data, content_type="application/json").status_code, 200)
            config = check.call_args.args[0]
            self.assertEqual(config.model, data["model"])
            self.assertEqual(config.api_key, self.environment["LLM_API_KEY"])
        self.assertEqual(Client().get(reverse("ai_settings")).json()["connection"]["model"], "workspace-env-model")

    @patch.dict(os.environ, environment | {"GEMINI_API_KEY": "test-gemini-key"})
    def test_catalogue_filters_provider_metadata_without_generation(self):
        with patch("openai.OpenAI") as openai, patch("google.genai.Client") as gemini:
            openai_client = openai.return_value.__enter__.return_value
            openai_client.models.list.return_value = [
                SimpleNamespace(id=model) for model in ("gpt-4o-mini", "whisper-1", "gpt-4o", "gpt-4o")
            ]
            self.assertEqual(list_ranking_models(ranking_config("openai")), ["gpt-4o", "gpt-4o-mini"])
            openai_client.chat.completions.create.assert_not_called()

            gemini_client = gemini.return_value.__enter__.return_value
            gemini_client.models.list.return_value = [
                SimpleNamespace(name="models/gemini-test", supported_actions=["generateContent"]),
                SimpleNamespace(name="models/gemini-test-tts", supported_actions=["generateContent"]),
                SimpleNamespace(name="models/embedding-test", supported_actions=["embedContent"]),
            ]
            self.assertEqual(list_ranking_models(ranking_config("gemini")), ["gemini-test"])
            gemini_client.models.generate_content.assert_not_called()

    @patch.dict(os.environ, environment | {"GEMINI_API_KEY": "test-gemini-key", "GEMINI_MODEL": "test-gemini-model"})
    def test_cloud_connection_checks_use_model_metadata(self):
        with patch("openai.OpenAI") as openai, patch("google.genai.Client") as gemini:
            openai_client = openai.return_value.__enter__.return_value
            openai_client.models.list.return_value = [SimpleNamespace(id="test-openai-model")]
            check_connection(ranking_config("openai"))
            self.assertEqual(openai.call_args.kwargs["base_url"], "https://api.openai.com/v1")
            openai_client.models.list.assert_called_once_with()
            openai_client.chat.completions.create.assert_not_called()

            gemini_client = gemini.return_value.__enter__.return_value
            check_connection(ranking_config("gemini"))
            gemini_client.models.get.assert_called_once_with(model="test-gemini-model")
            gemini_client.models.generate_content.assert_not_called()
