"""Remove abandoned upload/obsolete version directories older than an hour.

Run while the backend is stopped: python -m app.cleanup
"""

import re
import time

from .config import Settings
from .database import connect, initialize
from .documents.storage import remove_version


def cleanup(settings: Settings, minimum_age_seconds: int = 3600) -> int:
    initialize(settings)
    root = (settings.data_dir / "documents").resolve()
    if not root.is_dir():
        return 0
    with connect(settings) as db:
        active = {
            (row["id"], row["storage_id"])
            for row in db.execute("SELECT id, storage_id FROM documents")
        }
    removed = 0
    cutoff = time.time() - minimum_age_seconds
    for document in root.iterdir():
        if (
            document.is_symlink()
            or not document.is_dir()
            or not re.fullmatch(r"[0-9a-f]{32}", document.name)
        ):
            continue
        for version in document.iterdir():
            if (
                version.is_symlink()
                or not version.is_dir()
                or not re.fullmatch(r"[0-9a-f]{32}", version.name)
            ):
                continue
            if (document.name, version.name) in active or version.stat().st_mtime > cutoff:
                continue
            if not version.resolve().is_relative_to(root):
                raise RuntimeError("Storage cleanup target is outside the document root")
            remove_version(version)
            removed += not version.exists()
    return removed


if __name__ == "__main__":
    print(f"Removed {cleanup(Settings())} obsolete storage versions.")
