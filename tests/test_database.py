import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.config import Settings
from app.database import connect, initialize


class DatabaseMigrationTests(unittest.TestCase):
    def test_existing_sqlite_document_survives_schema_upgrade_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data_dir=Path(directory))
            with closing(sqlite3.connect(settings.db_path)) as db:
                db.executescript("""
                    CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
                    CREATE TABLE documents (id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                        name TEXT NOT NULL, revision INTEGER NOT NULL, storage_id TEXT NOT NULL,
                        page_count INTEGER NOT NULL, byte_size INTEGER NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                    PRAGMA user_version=1;
                """)
                db.execute("INSERT INTO users VALUES ('owner','local@example.com','hash','date')")
                db.execute(
                    "INSERT INTO documents VALUES ('doc','owner','Old',1,'v1',1,10,'date','date')"
                )
                db.commit()
            initialize(settings)
            initialize(settings)
            with connect(settings) as db:
                document = db.execute("SELECT * FROM documents WHERE id = ?", ("doc",)).fetchone()
                self.assertEqual(document["revision"], 1)
                self.assertIsNone(document["manifest"])
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(
                    db.execute("SELECT count(*) FROM storage_garbage").fetchone()[0], 0
                )
