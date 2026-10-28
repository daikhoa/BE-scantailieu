import mimetypes
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class WebHostingTests(unittest.TestCase):
    def test_web_assets_and_api_share_origin_without_exposing_runtime_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            web = root / "frontend"
            (web / "assets").mkdir(parents=True)
            (web / "index.html").write_text("<html>Docscan</html>", encoding="utf-8")
            (web / "assets" / "index-abc123.js").write_text("console.info('web')", encoding="utf-8")
            (web / "assets" / "runtime.mjs").write_text(
                "export const version = 1", encoding="utf-8"
            )
            data = root / "private-data"
            settings = Settings(data_dir=data, frontend_dir=web)
            with TestClient(create_app(settings)) as client:
                (data / "private.pdf").write_bytes(b"private")
                self.assertEqual(client.get("/").status_code, 200)
                self.assertIn("Docscan", client.get("/").text)
                self.assertEqual(client.get("/api/v1/health").json()["status"], "ok")
                self.assertEqual(client.get("/api/v1/documents").status_code, 401)
                self.assertEqual(client.get("/private.pdf").status_code, 404)
                asset = client.get("/assets/index-abc123.js")
                self.assertIn("immutable", asset.headers["cache-control"])
                self.assertEqual(client.get("/").headers["cache-control"], "no-store")
                mimetypes.init()
                with patch.dict(mimetypes.types_map, {".mjs": "text/plain"}):
                    module = client.get("/assets/runtime.mjs")
                self.assertEqual(module.headers["content-type"], "text/javascript; charset=utf-8")
                self.assertEqual(module.headers["x-content-type-options"], "nosniff")
