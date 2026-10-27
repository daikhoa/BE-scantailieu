import sqlite3
from contextlib import contextmanager

from .config import Settings


@contextmanager
def connect(settings: Settings):
    db = sqlite3.connect(settings.db_path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with connect(settings) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] > 1:
            raise RuntimeError(
                "Database schema is newer than this application; refusing to downgrade."
            )
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                kind TEXT NOT NULL, expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
            CREATE TABLE IF NOT EXISTS rate_limits (
                key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, reset_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                name TEXT NOT NULL, revision INTEGER NOT NULL, storage_id TEXT NOT NULL,
                page_count INTEGER NOT NULL, byte_size INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS documents_owner ON documents(user_id, updated_at DESC, id);
            CREATE TABLE IF NOT EXISTS create_receipts (
                user_id TEXT NOT NULL REFERENCES users(id), request_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL, document_id TEXT NOT NULL REFERENCES documents(id),
                PRIMARY KEY(user_id, request_key)
            );
            PRAGMA user_version = 1;
        """)
