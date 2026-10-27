import unittest
from unittest.mock import patch

from app.config import Settings


class CloudConfigTests(unittest.TestCase):
    def test_production_refuses_ephemeral_sqlite_and_local_uploads(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "PostgreSQL"):
                Settings(production=True)

    def test_production_requires_private_storage_and_secure_cookies(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Supabase"):
                Settings(production=True, database_url="postgresql://localhost/docscan")

    def test_cloud_configuration_hides_secrets_and_bounds_free_uploads(self):
        settings = Settings(
            production=True,
            database_url="postgresql://localhost/docscan",
            supabase_url="https://project.supabase.co",
            supabase_key="sb_secret_test_only_placeholder",
            secure_cookie=True,
        )
        self.assertTrue(settings.remote_storage)
        self.assertNotIn(settings.supabase_key, repr(settings))
        self.assertNotIn(settings.database_url, repr(settings))
        self.assertLessEqual(settings.max_pdf_bytes, 50_000_000)

    def test_render_url_is_an_allowed_browser_origin(self):
        with patch.dict("os.environ", {"RENDER_EXTERNAL_URL": "https://docscan.onrender.com"}):
            self.assertIn("https://docscan.onrender.com", Settings().allowed_origins)
