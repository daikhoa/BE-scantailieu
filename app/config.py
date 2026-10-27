import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DOCSCAN_DATA_DIR", "data")).resolve()
    )
    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            filter(
                None,
                os.getenv(
                    "DOCSCAN_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
                ).split(","),
            )
        )
    )
    secure_cookie: bool = field(
        default_factory=lambda: os.getenv("DOCSCAN_SECURE_COOKIE", "false").lower() == "true"
    )
    session_seconds: int = 7 * 24 * 3600
    max_request_bytes: int = 200 * 1024 * 1024
    max_file_bytes: int = 25 * 1024 * 1024
    max_pdf_bytes: int = 100 * 1024 * 1024
    max_pages: int = 50
    max_documents: int = 1000
    max_user_bytes: int = 2 * 1024 * 1024 * 1024

    @property
    def db_path(self) -> Path:
        return self.data_dir / "docscan.sqlite3"
