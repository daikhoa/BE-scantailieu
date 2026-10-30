import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from fastapi import HTTPException

from .config import Settings

logger = logging.getLogger(__name__)


class SupabaseStorage:
    def __init__(self, settings: Settings):
        self.settings = settings
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,62}", settings.storage_bucket):
            raise ValueError("Invalid private storage bucket name")

    def _request(self, method: str, path: str, **options) -> httpx.Response:
        headers = {"apikey": self.settings.supabase_key}
        if self.settings.supabase_key.startswith("eyJ"):
            headers["Authorization"] = "Bearer " + self.settings.supabase_key
        try:
            shared_client = options.pop("_client", None)
            with (
                nullcontext(shared_client)
                if shared_client
                else httpx.Client(
                    base_url=self.settings.supabase_url + "/storage/v1/",
                    headers=headers,
                    timeout=httpx.Timeout(45, connect=10),
                    follow_redirects=False,
                ) as client
            ):
                return client.request(method, path, **options)
        except httpx.HTTPError:
            raise HTTPException(503, "Không kết nối được kho tài liệu. Vui lòng thử lại.") from None

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if not response.is_success:
            logger.error(
                "Supabase Storage returned HTTP %d for %s",
                response.status_code,
                response.request.method,
            )
            if response.status_code == 413:
                raise HTTPException(413, "Tệp vượt giới hạn dung lượng của kho lưu trữ.")
            raise HTTPException(503, "Kho tài liệu chưa sẵn sàng hoặc đã đầy. Vui lòng thử lại.")

    def ensure_private_bucket(self) -> None:
        response = self._request("GET", "bucket/" + self.settings.storage_bucket)
        if response.status_code == 404:
            response = self._request(
                "POST",
                "bucket",
                json={
                    "id": self.settings.storage_bucket,
                    "name": self.settings.storage_bucket,
                    "public": False,
                    "file_size_limit": 50_000_000,
                    "allowed_mime_types": [
                        "application/pdf",
                        "application/json",
                        "image/jpeg",
                        "image/png",
                        "image/webp",
                        "image/avif",
                        "image/heic",
                        "image/heif",
                    ],
                },
            )
            self._check(response)
            return
        self._check(response)
        if response.json().get("public") is not False:
            raise ValueError("Document storage bucket must be private")

    def upload(self, path: Path, key: str, mime: str, *, client=None) -> None:
        with path.open("rb") as source:
            response = self._request(
                "POST",
                "object/" + quote(self.settings.storage_bucket + "/" + key, safe="/"),
                content=iter(lambda: source.read(65536), b""),
                _client=client,
                headers={
                    "Content-Type": mime,
                    "Content-Length": str(path.stat().st_size),
                    "x-upsert": "false",
                    "cache-control": "private, no-store",
                },
            )
        self._check(response)

    def upload_many(self, files: list[tuple[Path, str, str]]) -> None:
        # HTTPX clients support sharing between threads. Reuse TLS connections and bound
        # simultaneous file streams; wait for workers before staged files are removed.
        headers = {"apikey": self.settings.supabase_key}
        if self.settings.supabase_key.startswith("eyJ"):
            headers["Authorization"] = "Bearer " + self.settings.supabase_key
        with (
            httpx.Client(
                base_url=self.settings.supabase_url + "/storage/v1/",
                headers=headers,
                timeout=httpx.Timeout(45, connect=10),
                follow_redirects=False,
            ) as client,
            ThreadPoolExecutor(max_workers=4) as pool,
        ):

            def send(file):
                self.upload(*file, client=client)

            for _ in pool.map(send, files):
                pass

    def signed_url(self, key: str, download_name: str | None = None) -> str:
        object_path = quote(self.settings.storage_bucket + "/" + key, safe="/")
        options = {"expiresIn": 60}
        if download_name:
            options["download"] = download_name
        response = self._request("POST", "object/sign/" + object_path, json=options)
        self._check(response)
        try:
            signed = response.json()["signedURL"]
            if not isinstance(signed, str) or not signed.startswith(
                "/object/sign/" + object_path + "?"
            ):
                raise ValueError("Unexpected storage redirect")
        except (ValueError, KeyError, TypeError):
            raise HTTPException(503, "Không thể mở tệp tài liệu. Vui lòng thử lại.") from None
        url = self.settings.supabase_url + "/storage/v1" + signed
        if download_name:
            url += "&" + urlencode({"download": download_name})
        return url

    def remove(self, keys: list[str]) -> None:
        if keys:
            self._check(
                self._request(
                    "DELETE", "object/" + self.settings.storage_bucket, json={"prefixes": keys}
                )
            )

    def download(self, key: str, target: Path, maximum: int, *, client=None) -> None:
        # Authenticated object reads stay on the configured private bucket, never a client URL.
        headers = {"apikey": self.settings.supabase_key}
        if self.settings.supabase_key.startswith("eyJ"):
            headers["Authorization"] = "Bearer " + self.settings.supabase_key
        try:
            with (
                nullcontext(client)
                if client
                else httpx.Client(timeout=httpx.Timeout(45, connect=10), follow_redirects=False)
            ) as client:
                with client.stream(
                    "GET",
                    self.settings.supabase_url
                    + "/storage/v1/object/authenticated/"
                    + quote(self.settings.storage_bucket + "/" + key, safe="/"),
                    headers=headers,
                ) as response:
                    self._check(response)
                    size = 0
                    with target.open("xb") as destination:
                        for chunk in response.iter_bytes(65536):
                            size += len(chunk)
                            if size > maximum:
                                raise HTTPException(413, "Tệp vượt giới hạn dung lượng.")
                            destination.write(chunk)
        except httpx.HTTPError:
            raise HTTPException(503, "Không kết nối được kho tài liệu. Vui lòng thử lại.") from None
