"""Durable cleanup candidates for immutable cloud versions, including interrupted uploads."""

import json
import time

from .config import Settings
from .database import connect
from .object_storage import SupabaseStorage


def remember(db, document_id: str, storage_id: str, filenames: list[str]) -> None:
    db.execute(
        "INSERT INTO storage_garbage VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
        (document_id, storage_id, json.dumps(filenames), int(time.time())),
    )


def collect_version(settings: Settings, document_id: str, storage_id: str) -> bool:
    with connect(settings) as db:
        live = db.execute(
            "SELECT id FROM documents WHERE id = ? AND storage_id = ?", (document_id, storage_id)
        ).fetchone()
        candidate = db.execute(
            "SELECT filenames FROM storage_garbage WHERE document_id = ? AND storage_id = ?",
            (document_id, storage_id),
        ).fetchone()
    if live or not candidate:
        return False
    prefix = f"{document_id}/{storage_id}/"
    SupabaseStorage(settings).remove(
        [prefix + filename for filename in json.loads(candidate["filenames"])]
    )
    with connect(settings) as db:
        db.execute(
            "DELETE FROM storage_garbage WHERE document_id = ? AND storage_id = ?",
            (document_id, storage_id),
        )
    return True


def collect_stale(settings: Settings, minimum_age_seconds: int = 3600) -> int:
    with connect(settings) as db:
        rows = db.execute(
            "SELECT document_id, storage_id FROM storage_garbage WHERE created_at <= ? ORDER BY created_at LIMIT 50",
            (int(time.time()) - minimum_age_seconds,),
        ).fetchall()
    return sum(collect_version(settings, row["document_id"], row["storage_id"]) for row in rows)
