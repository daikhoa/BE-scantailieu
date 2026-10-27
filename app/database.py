import hashlib
import sqlite3
from contextlib import contextmanager
from typing import LiteralString

import psycopg
from psycopg.rows import dict_row

from .config import Settings

INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg.IntegrityError)
DATABASE_ERRORS = (sqlite3.Error, psycopg.Error)
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL, expires_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
CREATE TABLE IF NOT EXISTS rate_limits (
    key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, reset_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL, revision INTEGER NOT NULL, storage_id TEXT NOT NULL,
    page_count INTEGER NOT NULL, byte_size BIGINT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, manifest TEXT
);
CREATE INDEX IF NOT EXISTS documents_owner ON documents(user_id, updated_at DESC, id);
CREATE TABLE IF NOT EXISTS create_receipts (
    user_id TEXT NOT NULL REFERENCES users(id), request_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL, document_id TEXT NOT NULL REFERENCES documents(id),
    PRIMARY KEY(user_id, request_key)
);
CREATE TABLE IF NOT EXISTS storage_garbage (
    document_id TEXT NOT NULL, storage_id TEXT NOT NULL,
    filenames TEXT NOT NULL, created_at BIGINT NOT NULL,
    PRIMARY KEY(document_id, storage_id)
);
"""


class PostgresConnection:
    def __init__(self, connection: psycopg.Connection):
        self.connection = connection

    def execute(self, statement: LiteralString, parameters: tuple = ()):
        # Only internal SQL uses these qmark placeholders; no question marks in SQL literals.
        # Values are always bound by the driver, never interpolated into the statement.
        if statement.count("?") != len(parameters):
            raise ValueError("SQL placeholder count does not match parameters")
        return self.connection.execute(statement.replace("?", "%s"), parameters or None)


def _postgres(settings: Settings):
    options = {"sslmode": "require"} if settings.production else {}
    if settings.database_password:
        options["password"] = settings.database_password
    return psycopg.connect(
        settings.database_url,
        connect_timeout=10,
        row_factory=dict_row,
        prepare_threshold=None,
        **options,
    )


@contextmanager
def connect(settings: Settings):
    if settings.database_url:
        with _postgres(settings) as db:
            db.execute("SET LOCAL search_path TO docscan, pg_catalog")
            db.execute("SET LOCAL statement_timeout TO '15s'")
            db.execute("SET LOCAL idle_in_transaction_session_timeout TO '30s'")
            yield PostgresConnection(db)
        return
    db = sqlite3.connect(settings.db_path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def write_lock(db, key: str) -> None:
    if isinstance(db, PostgresConnection):
        identifier = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], signed=True)
        db.execute("SELECT pg_advisory_xact_lock(?)", (identifier,))
    else:
        db.execute("BEGIN IMMEDIATE")


def initialize(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.database_url:
        with _postgres(settings) as db:
            db.execute("SET LOCAL statement_timeout TO '15s'")
            db.execute("SELECT pg_advisory_xact_lock(6478271001)")
            db.execute("CREATE SCHEMA IF NOT EXISTS docscan")
            db.execute("SET LOCAL search_path TO docscan, pg_catalog")
            db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
            version = db.execute("SELECT max(version) AS version FROM schema_version").fetchone()
            if version["version"] and version["version"] > 2:
                raise RuntimeError("Database schema is newer than this application")
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    db.execute(statement)
            for table in (
                "users",
                "sessions",
                "rate_limits",
                "documents",
                "create_receipts",
                "storage_garbage",
                "schema_version",
            ):
                db.execute(
                    psycopg.sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(
                        psycopg.sql.Identifier(table)
                    )
                )
            db.execute("INSERT INTO schema_version VALUES (2) ON CONFLICT DO NOTHING")
        return
    with connect(settings) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] > 2:
            raise RuntimeError("Database schema is newer than this application")
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(SCHEMA)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(documents)")}
        if "manifest" not in columns:
            db.execute("ALTER TABLE documents ADD COLUMN manifest TEXT")
        db.execute("PRAGMA user_version = 2")
