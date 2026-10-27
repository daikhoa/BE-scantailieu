import json
from dataclasses import replace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from test_auth import ApiTestCase
from test_documents import upload_payload

HTTP_CLIENT = httpx.Client


class CloudDocumentTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.client.close()
        self.objects = {}
        self.upload_error = False
        self.transport_patch = patch(
            "app.object_storage.httpx.Client",
            side_effect=lambda **options: HTTP_CLIENT(
                **options, transport=httpx.MockTransport(self.storage_request)
            ),
        )
        self.transport_patch.start()
        self.settings = replace(
            self.settings,
            supabase_url="https://project.supabase.co",
            supabase_key="sb_secret_mock_transport_only",
        )
        from app.main import create_app

        self.client = TestClient(create_app(self.settings))
        self.register()
        self.login()

    def tearDown(self):
        self.transport_patch.stop()
        super().tearDown()

    def storage_request(self, request):
        path = request.url.path.removeprefix("/storage/v1/")
        if path == "bucket/docscan-private":
            return httpx.Response(200, json={"public": False})
        if request.method == "DELETE":
            for key in json.loads(request.content)["prefixes"]:
                self.objects.pop("docscan-private/" + key, None)
            return httpx.Response(200, json=[])
        if path.startswith("object/sign/"):
            key = path.removeprefix("object/sign/")
            if key not in self.objects:
                return httpx.Response(404)
            return httpx.Response(200, json={"signedURL": "/object/sign/" + key + "?token=mock"})
        if path.startswith("object/") and request.method == "POST":
            if self.upload_error:
                return httpx.Response(503)
            self.objects[path.removeprefix("object/")] = request.content
            return httpx.Response(200, json={})
        return httpx.Response(404)

    def save(self, identifier=None, revision=None):
        fields, files = upload_payload(pages=1, revision=revision)
        path = "/api/v1/documents" + ("/" + identifier if identifier else "")
        return self.client.request(
            "PUT" if identifier else "POST", path, data=fields, files=files, headers=self.headers
        )

    def test_cloud_document_reopens_without_local_files_and_download_is_private(self):
        created = self.save()
        self.assertEqual(created.status_code, 201, created.text)
        identifier = created.json()["id"]
        self.assertEqual(len(self.objects), 5)
        self.assertFalse(any((self.settings.data_dir / "documents").rglob("manifest.json")))
        self.client.close()
        from app.main import create_app

        self.client = TestClient(create_app(self.settings))
        self.login()
        detail = self.client.get("/api/v1/documents/" + identifier).json()
        self.assertEqual(detail["pages"][0]["recipe"]["rotation"], 0)
        response = self.client.get(detail["pdfUrl"] + "?download=true", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertIn("download=", response.headers["location"])
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.client.delete("/api/v1/auth/session", headers=self.headers)
        self.register("outsider@example.com")
        self.login("outsider@example.com")
        self.assertEqual(self.client.get(detail["pdfUrl"], follow_redirects=False).status_code, 404)

    def test_failed_upload_preserves_live_revision_and_success_retires_old_objects(self):
        created = self.save().json()
        original_objects = dict(self.objects)
        self.upload_error = True
        self.assertEqual(self.save(created["id"], 1).status_code, 503)
        self.assertEqual(self.objects, original_objects)
        self.assertEqual(
            self.client.get("/api/v1/documents/" + created["id"]).json()["revision"], 1
        )
        self.upload_error = False
        updated = self.save(created["id"], 1)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["revision"], 2)
        self.assertEqual(len(self.objects), 5)
        self.assertTrue(set(self.objects).isdisjoint(original_objects))
