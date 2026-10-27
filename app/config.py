import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def allowed_origins() -> tuple[str, ...]:
    configured = os.getenv(
        "DOCSCAN_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    return tuple(
        dict.fromkeys(
            origin.strip()
            for origin in [*configured, os.getenv("RENDER_EXTERNAL_URL", "")]
            if origin.strip()
        )
    )


@dataclass(frozen=True)
class Settings:
    production: bool = field(default_factory=lambda: os.getenv("DOCSCAN_ENV") == "production")
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""), repr=False)
    database_password: str = field(
        default_factory=lambda: os.getenv("DATABASE_PASSWORD", ""), repr=False
    )
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", "").rstrip("/"))
    supabase_key: str = field(
        default_factory=lambda: os.getenv("SUPABASE_SECRET_KEY", ""), repr=False
    )
    storage_bucket: str = field(
        default_factory=lambda: os.getenv("SUPABASE_STORAGE_BUCKET", "docscan-private")
    )
    frontend_dir: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["DOCSCAN_FRONTEND_DIR"]).resolve()
            if os.getenv("DOCSCAN_FRONTEND_DIR")
            else None
        )
    )
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DOCSCAN_DATA_DIR", "data")).resolve()
    )
    allowed_origins: tuple[str, ...] = field(default_factory=allowed_origins)
    secure_cookie: bool = field(
        default_factory=lambda: os.getenv("DOCSCAN_SECURE_COOKIE", "false").lower() == "true"
    )
    session_seconds: int = 7 * 24 * 3600
    max_request_bytes: int = 200 * 1024 * 1024
    max_file_bytes: int = 25 * 1024 * 1024
    max_pdf_bytes: int = 50_000_000
    max_pages: int = 50
    max_documents: int = 1000
    max_user_bytes: int = 2 * 1024 * 1024 * 1024

    def __post_init__(self):
        if self.database_url and not self.database_url.startswith(("postgres://", "postgresql://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL connection string.")
        if bool(self.supabase_url) != bool(self.supabase_key):
            raise ValueError("Configure both Supabase URL and server secret key.")
        if self.remote_storage:
            url = urlsplit(self.supabase_url)
            if (
                url.scheme != "https"
                or not (url.hostname or "").endswith(".supabase.co")
                or url.username
                or url.password
                or url.port
                or url.query
                or url.fragment
                or url.path not in ("", "/")
            ):
                raise ValueError("Supabase URL must be the HTTPS project URL.")
            if not self.supabase_key.startswith(("sb_secret_", "eyJ")):
                raise ValueError("Use a Supabase server secret, never a publishable key.")
        if self.production:
            if not self.database_url:
                raise ValueError("Production requires PostgreSQL; SQLite is for local use only.")
            if not self.remote_storage:
                raise ValueError("Production requires private Supabase Storage.")
            if not self.secure_cookie:
                raise ValueError("Production requires Secure cookies.")
        if self.frontend_dir and not (self.frontend_dir / "index.html").is_file():
            raise ValueError("Frontend build is missing index.html.")

    @property
    def remote_storage(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "docscan.sqlite3"
