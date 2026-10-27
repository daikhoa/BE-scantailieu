import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app.config import Settings
from app.object_storage import SupabaseStorage

HTTP_CLIENT = httpx.Client


class SupabaseStorageTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            supabase_url="https://project.supabase.co",
            supabase_key="sb_secret_placeholder_for_mock_transport",
        )
        self.requests = []

    def transport(self, handler):
        def capture(request):
            self.requests.append(request)
            return handler(request)

        return patch(
            "app.object_storage.httpx.Client",
            side_effect=lambda **options: HTTP_CLIENT(
                **options, transport=httpx.MockTransport(capture)
            ),
        )

    def test_upload_preserves_bytes_and_uses_server_key_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "page"
            path.write_bytes(b"image bytes\r\n")
            with self.transport(lambda request: httpx.Response(200, json={})):
                SupabaseStorage(self.settings).upload(path, "a/b/original_0", "image/jpeg")
        request = self.requests[0]
        self.assertEqual(request.content, b"image bytes\r\n")
        self.assertEqual(request.headers["apikey"], self.settings.supabase_key)
        self.assertNotIn("authorization", request.headers)
        self.assertEqual(request.headers["x-upsert"], "false")

    def test_signed_download_is_short_lived_and_keeps_unicode_filename(self):
        with self.transport(
            lambda request: httpx.Response(
                200,
                json={
                    "signedURL": "/object/sign/docscan-private/a/b/document.pdf?token=mock-token"
                },
            )
        ):
            url = SupabaseStorage(self.settings).signed_url("a/b/document.pdf", "Tài liệu.pdf")
        self.assertIn("download=T%C3%A0i+li%E1%BB%87u.pdf", url)
        self.assertEqual(
            json.loads(self.requests[0].content), {"expiresIn": 60, "download": "Tài liệu.pdf"}
        )
        self.assertNotIn(self.settings.supabase_key, url)

    def test_rejects_public_bucket_and_unexpected_signed_url(self):
        for body, operation in [
            ({"public": True}, lambda storage: storage.ensure_private_bucket()),
            (
                {"signedURL": "https://evil.example/file"},
                lambda storage: storage.signed_url("a/b/x"),
            ),
        ]:
            with self.transport(lambda request: httpx.Response(200, json=body)):
                with self.assertRaises((ValueError, HTTPException)):
                    operation(SupabaseStorage(self.settings))

    def test_storage_failure_returns_safe_error_without_provider_payload(self):
        with (
            self.transport(lambda request: httpx.Response(503, text=self.settings.supabase_key)),
            self.assertLogs("app.object_storage", level="ERROR") as logs,
        ):
            with self.assertRaises(HTTPException) as raised:
                SupabaseStorage(self.settings).signed_url("a/b/x")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn(self.settings.supabase_key, str(raised.exception))
        self.assertIn("HTTP 503", logs.output[0])
        self.assertNotIn(self.settings.supabase_key, " ".join(logs.output))
