import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            data_dir=Path(self.temp.name), allowed_origins=("http://testserver",)
        )
        self.client = TestClient(create_app(self.settings))
        self.headers = {"Origin": "http://testserver", "X-Requested-With": "docscan"}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def register(self, email="person@example.com"):
        return self.client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "correct horse battery"},
            headers=self.headers,
        )

    def login(self, email="person@example.com", password="correct horse battery"):
        return self.client.post(
            "/api/v1/auth/session",
            json={"email": email, "password": password},
            headers=self.headers,
        )


class AuthTests(ApiTestCase):
    def test_register_login_logout_and_no_password_in_response(self):
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)
        response = self.register()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.json()), {"id", "email"})
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/v1/auth/me").json()["email"], "person@example.com")
        self.assertEqual(
            self.client.delete("/api/v1/auth/session", headers=self.headers).status_code, 204
        )
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_validation_duplicate_email_and_wrong_password(self):
        self.assertEqual(self.register("broken").status_code, 422)
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/register",
                json={"email": "ok@example.com", "password": "short"},
                headers=self.headers,
            ).status_code,
            422,
        )
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register("PERSON@example.com").status_code, 409)
        self.assertEqual(self.login(password="wrong password").status_code, 401)

    def test_browser_mutations_require_same_origin_and_custom_header(self):
        payload = {"email": "other@example.com", "password": "correct horse battery"}
        self.assertEqual(self.client.post("/api/v1/auth/register", json=payload).status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/register",
                json=payload,
                headers={"Origin": "https://evil.example", "X-Requested-With": "docscan"},
            ).status_code,
            403,
        )

    def test_native_bearer_uses_same_api_and_can_be_revoked(self):
        self.register()
        response = self.client.post(
            "/api/v1/auth/token",
            json={"email": "person@example.com", "password": "correct horse battery"},
        )
        self.assertEqual(response.status_code, 200)
        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=headers).status_code, 200)
        revoked = self.client.delete("/api/v1/auth/session", headers=headers)
        self.assertEqual(revoked.status_code, 204)
        self.assertNotIn("set-cookie", revoked.headers)
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=headers).status_code, 401)

    def test_login_is_rate_limited(self):
        for _ in range(10):
            self.login()
        self.assertEqual(self.login().status_code, 429)

    def test_expired_session_and_cookie_csrf(self):
        from app.database import connect

        self.register()
        self.login()
        self.assertEqual(self.client.delete("/api/v1/auth/session").status_code, 403)
        with connect(self.settings) as db:
            db.execute("UPDATE sessions SET expires_at = 0")
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
